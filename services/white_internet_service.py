"""Business logic orchestration for White Internet subscriptions, quotas, and client integration."""

from __future__ import annotations

import json
import logging
import secrets
import urllib.parse
import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from config.constants import (
    WHITE_INTERNET_BASE_DURATION_DAYS,
    WHITE_INTERNET_BASE_PRICE_RUB,
    WHITE_INTERNET_BASE_TRAFFIC_BYTES,
    WHITE_INTERNET_MAX_QUOTA_BYTES,
    WHITE_INTERNET_SERVICE_TYPE,
    WHITE_INTERNET_TOPUP_PACKS,
)
from config.enums import (
    ServerHealthState,
    TariffQuoteOperation,
    TariffQuoteStatus,
    WhiteInternetStatus,
)
from database.models import (
    Server,
    Tariff,
    TariffQuote,
    WhiteInternetQuotaGrant,
    WhiteInternetSubscription,
)
from database.repositories import white_internet_repo
from database.repositories.account_ledger_repo import (
    AccountLedgerError,
    InsufficientAccountBalanceError,
    create_purchase_debit,
)
from database.repositories.tariff_quotes_repo import get_or_create_current_version, lock_checkout_user
from utils.datetime_helpers import now_utc

logger = logging.getLogger(__name__)


class WhiteInternetService:
    """Service managing the White Internet product lifecycle."""

    @staticmethod
    async def get_or_create_white_internet_tariff(
        session: AsyncSession,
        *,
        price_rub: Decimal = WHITE_INTERNET_BASE_PRICE_RUB,
        duration_days: int = WHITE_INTERNET_BASE_DURATION_DAYS,
    ) -> Tariff:
        """Find or create the canonical White Internet tariff."""
        stmt = (
            select(Tariff)
            .where(
                Tariff.service_type == WHITE_INTERNET_SERVICE_TYPE,
                Tariff.duration_days == duration_days,
                Tariff.device_limit == 1,
            )
            .limit(1)
        )
        result = await session.execute(stmt)
        tariff = result.scalar_one_or_none()
        if tariff is not None:
            return tariff

        tariff = Tariff(
            name=texts.WL_DEFAULT_TARIFF_NAME,
            service_type=WHITE_INTERNET_SERVICE_TYPE,
            device_limit=1,
            duration_days=duration_days,
            price_rub=price_rub,
            is_active=True,
            sort_order=100,
        )
        session.add(tariff)
        await session.flush()
        return tariff

    @staticmethod
    async def select_origin_node(session: AsyncSession) -> Server:
        """Select a suitable server capable of hosting Xray Origin."""
        # Prefer servers explicitly tagged with capabilities containing 'xray_origin'
        stmt = select(Server).where(
            Server.health_state.in_([ServerHealthState.ONLINE, ServerHealthState.WAITING_CONFIRMATION])
        )
        result = await session.execute(stmt)
        servers = result.scalars().all()

        for s in servers:
            caps = s.capabilities or []
            if "xray_origin" in caps:
                return s

        # Fallback to any active server if capabilities are not populated yet
        if servers:
            return servers[0]

        # Last resort fallback to any server
        stmt_any = select(Server).order_by(Server.id.asc()).limit(1)
        res_any = await session.execute(stmt_any)
        server = res_any.scalar_one_or_none()
        if server is None:
            raise RuntimeError("No server nodes available for White Internet origin deployment.")
        return server

    @classmethod
    async def purchase_subscription(
        cls,
        session: AsyncSession,
        user_id: int,
    ) -> tuple[bool, str, WhiteInternetSubscription | None]:
        """
        Purchase and activate a White Internet subscription:
        1. Lock user account
        2. Ensure no conflicting active subscription exists
        3. Create quote and settle purchase debit
        4. Create subscription in PENDING status with BASE quota grant
        """
        user = await lock_checkout_user(session, user_id)
        if user is None:
            return False, texts.WL_USER_NOT_FOUND, None

        # Check existing subscription
        existing = await white_internet_repo.get_subscription_by_user_id(session, user_id)
        now = now_utc()
        if existing and existing.status == WhiteInternetStatus.ACTIVE and existing.expires_at > now:
            return False, texts.WL_ALREADY_ACTIVE, existing

        tariff = await cls.get_or_create_white_internet_tariff(session)
        tariff_version = await get_or_create_current_version(session, tariff)
        origin_node = await cls.select_origin_node(session)

        # Create financial quote
        quote = TariffQuote(
            operation_type=TariffQuoteOperation.PURCHASE,
            service_type=WHITE_INTERNET_SERVICE_TYPE,
            user_id=user.id,
            source_tariff_version_id=None,
            target_tariff_version_id=tariff_version.id,
            final_price_rub=tariff.price_rub,
            status=TariffQuoteStatus.ACTIVE,
        )
        session.add(quote)
        await session.flush()

        # Debit balance
        try:
            debit, _ = await create_purchase_debit(
                session, user_id=user.id, quote_id=quote.id, amount=quote.final_price_rub
            )
        except InsufficientAccountBalanceError:
            quote.status = TariffQuoteStatus.CANCELLED
            await session.flush()
            return False, texts.WL_INSUFFICIENT_BALANCE_BUY.format(
                price=int(tariff.price_rub),
                balance=user.balance_rub,
                shortage=tariff.price_rub - user.balance_rub,
            ), None
        except AccountLedgerError as exc:
            quote.status = TariffQuoteStatus.CANCELLED
            await session.flush()
            return False, f"{texts.WL_DEBIT_FAILED}: {exc}", None

        # Mark quote as consumed
        quote.status = TariffQuoteStatus.CONSUMED

        # Generate unique token and uuid
        token = secrets.token_hex(32)
        client_uuid = str(uuid.uuid4())

        sub = await white_internet_repo.create_white_internet_subscription(
            session,
            user_id=user.id,
            origin_node_id=origin_node.id,
            token=token,
            uuid=client_uuid,
            quote_id=quote.id,
            price_rub=tariff.price_rub,
            duration_days=tariff.duration_days,
            base_bytes=WHITE_INTERNET_BASE_TRAFFIC_BYTES,
        )

        logger.info("White Internet subscription purchased for user %d (sub_id=%d)", user_id, sub.id)
        return True, texts.WL_BUY_SUCCESS, sub

    @classmethod
    async def renew_subscription(
        cls,
        session: AsyncSession,
        user_id: int,
    ) -> tuple[bool, str, WhiteInternetSubscription | None]:
        """
        Renew an existing White Internet subscription (+30 days, fresh 50 GiB base, carryover top-ups).
        """
        user = await lock_checkout_user(session, user_id)
        if user is None:
            return False, texts.WL_USER_NOT_FOUND, None

        sub = await white_internet_repo.get_subscription_by_user_id(session, user_id)
        if sub is None:
            return False, texts.WL_SUB_NOT_FOUND, None

        tariff = await cls.get_or_create_white_internet_tariff(session)
        tariff_version = await get_or_create_current_version(session, tariff)

        # Create financial quote
        quote = TariffQuote(
            operation_type=TariffQuoteOperation.RENEW,
            service_type=WHITE_INTERNET_SERVICE_TYPE,
            user_id=user.id,
            source_tariff_version_id=tariff_version.id,
            target_tariff_version_id=tariff_version.id,
            final_price_rub=tariff.price_rub,
            status=TariffQuoteStatus.ACTIVE,
        )
        session.add(quote)
        await session.flush()

        # Debit balance
        try:
            debit, _ = await create_purchase_debit(
                session, user_id=user.id, quote_id=quote.id, amount=quote.final_price_rub
            )
        except InsufficientAccountBalanceError:
            quote.status = TariffQuoteStatus.CANCELLED
            await session.flush()
            return False, texts.WL_INSUFFICIENT_BALANCE_RENEW.format(
                price=int(tariff.price_rub),
                balance=user.balance_rub,
                shortage=tariff.price_rub - user.balance_rub,
            ), None
        except AccountLedgerError as exc:
            quote.status = TariffQuoteStatus.CANCELLED
            await session.flush()
            return False, f"{texts.WL_DEBIT_FAILED}: {exc}", None

        quote.status = TariffQuoteStatus.CONSUMED

        renewed_sub = await white_internet_repo.renew_subscription_atomic(
            session,
            subscription_id=sub.id,
            quote_id=quote.id,
            price_rub=tariff.price_rub,
            duration_days=tariff.duration_days,
            base_bytes=WHITE_INTERNET_BASE_TRAFFIC_BYTES,
        )

        logger.info("White Internet subscription renewed for user %d (sub_id=%d)", user_id, renewed_sub.id)
        return True, texts.WL_RENEW_SUCCESS, renewed_sub

    @classmethod
    async def topup_quota(
        cls,
        session: AsyncSession,
        user_id: int,
        pack_gb: int,
    ) -> tuple[bool, str, WhiteInternetQuotaGrant | None]:
        """
        Purchase additional traffic package (+10, +25, +50 GiB).
        Enforces Cap 500 GiB and carries over with subscription expiration.
        """
        if pack_gb not in WHITE_INTERNET_TOPUP_PACKS:
            return False, texts.WL_INVALID_TOPUP_PACK.format(gb=pack_gb), None

        pack_price = WHITE_INTERNET_TOPUP_PACKS[pack_gb]

        user = await lock_checkout_user(session, user_id)
        if user is None:
            return False, texts.WL_USER_NOT_FOUND, None

        sub = await white_internet_repo.get_subscription_by_user_id(session, user_id)
        if sub is None:
            return False, texts.WL_NO_SUB, None

        now = now_utc()
        if sub.status == WhiteInternetStatus.EXPIRED or sub.expires_at <= now:
            return False, texts.WL_SUB_EXPIRED, None

        # Check Cap 500 GiB before creating financial debit
        current_available = await white_internet_repo.get_available_quota_bytes(session, sub.id, now)
        pack_bytes = pack_gb * 1024 * 1024 * 1024
        if current_available + pack_bytes > WHITE_INTERNET_MAX_QUOTA_BYTES:
            return (
                False,
                texts.WL_CAP_EXCEEDED.format(gb=pack_gb, available=current_available // (1024**3)),
                None,
            )

        tariff = await cls.get_or_create_white_internet_tariff(session)
        tariff_version = await get_or_create_current_version(session, tariff)

        # Create financial quote for top-up
        quote = TariffQuote(
            operation_type=TariffQuoteOperation.PURCHASE,
            service_type=WHITE_INTERNET_SERVICE_TYPE,
            user_id=user.id,
            source_tariff_version_id=tariff_version.id,
            target_tariff_version_id=tariff_version.id,
            final_price_rub=pack_price,
            status=TariffQuoteStatus.ACTIVE,
        )
        session.add(quote)
        await session.flush()

        # Debit balance
        try:
            debit, _ = await create_purchase_debit(
                session, user_id=user.id, quote_id=quote.id, amount=quote.final_price_rub
            )
        except InsufficientAccountBalanceError:
            quote.status = TariffQuoteStatus.CANCELLED
            await session.flush()
            return False, texts.WL_INSUFFICIENT_BALANCE_TOPUP.format(
                gb=pack_gb,
                price=int(pack_price),
                balance=user.balance_rub,
                shortage=pack_price - user.balance_rub,
            ), None
        except AccountLedgerError as exc:
            quote.status = TariffQuoteStatus.CANCELLED
            await session.flush()
            return False, f"{texts.WL_DEBIT_FAILED}: {exc}", None

        quote.status = TariffQuoteStatus.CONSUMED

        try:
            grant = await white_internet_repo.topup_quota_atomic(
                session,
                subscription_id=sub.id,
                quote_id=quote.id,
                pack_gb=pack_gb,
                price_rub=pack_price,
            )
        except white_internet_repo.WhiteInternetQuotaCapExceededError as exc:
            return False, str(exc), None

        logger.info("White Internet quota topped up by %d GB for user %d (sub_id=%d)", pack_gb, user_id, sub.id)
        return True, texts.WL_TOPUP_SUCCESS.format(gb=pack_gb), grant

    @staticmethod
    def generate_vless_links(
        subscription: WhiteInternetSubscription,
        cdn_domain: str,
        port: int = 443,
    ) -> list[str]:
        """
        Generate standard VLESS XHTTP links for Germany and Netherlands.
        Includes complete 'extra' parameters for Yandex CDN and client compliance.
        """
        extra_dict = {
            "uplinkHTTPMethod": "OPTIONS",
            "xPaddingObfsMode": True,
            "xPaddingKey": "dc",
            "xPaddingHeader": "X-Cache",
            "xPaddingMethod": "tokenish",
        }
        extra_param = urllib.parse.quote(json.dumps(extra_dict, separators=(",", ":")))
        tag_de = urllib.parse.quote(texts.WL_VLESS_TAG_DE)
        tag_nl = urllib.parse.quote(texts.WL_VLESS_TAG_NL)

        link_de = (
            f"vless://{subscription.uuid}@{cdn_domain}:{port}"
            f"?encryption=none"
            f"&security=tls"
            f"&sni={cdn_domain}"
            f"&type=xhttp"
            f"&path=%2Fapi%2Fv3%2Fde"
            f"&mode=packet-up"
            f"&extra={extra_param}"
            f"#{tag_de}"
        )

        link_nl = (
            f"vless://{subscription.uuid}@{cdn_domain}:{port}"
            f"?encryption=none"
            f"&security=tls"
            f"&sni={cdn_domain}"
            f"&type=xhttp"
            f"&path=%2Fapi%2Fv3%2Fnl"
            f"&mode=packet-up"
            f"&extra={extra_param}"
            f"#{tag_nl}"
        )

        return [link_de, link_nl]
