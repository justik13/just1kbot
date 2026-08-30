"""Business logic orchestration for White Internet subscriptions, quotas, and client integration."""

from __future__ import annotations

import json
import logging
import secrets
import urllib.parse
import uuid
from datetime import timedelta
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
from config.enums import ServerHealthState, TariffQuoteOperation, TariffQuoteStatus, WhiteInternetStatus
from database.models import Server, Tariff, TariffQuote, WhiteInternetSubscription
from database.repositories import white_internet_repo
from database.repositories.account_ledger_repo import AccountLedgerError, InsufficientAccountBalanceError, create_purchase_debit
from database.repositories.tariff_quotes_repo import get_or_create_current_version, lock_checkout_user
from utils.datetime_helpers import now_utc

logger = logging.getLogger(__name__)


class WhiteInternetService:
    """Service managing the White Internet product lifecycle."""

    @staticmethod
    async def get_or_create_white_internet_tariff(session: AsyncSession, *, price_rub: Decimal = WHITE_INTERNET_BASE_PRICE_RUB, duration_days: int = WHITE_INTERNET_BASE_DURATION_DAYS) -> Tariff:
        stmt = select(Tariff).where(Tariff.service_type == WHITE_INTERNET_SERVICE_TYPE, Tariff.duration_days == duration_days, Tariff.device_limit == 1).limit(1)
        tariff = (await session.execute(stmt)).scalar_one_or_none()
        if tariff is not None:
            return tariff
        tariff = Tariff(name=texts.WL_DEFAULT_TARIFF_NAME, service_type=WHITE_INTERNET_SERVICE_TYPE, device_limit=1, duration_days=duration_days, price_rub=int(price_rub), is_active=True, sort_order=100)
        session.add(tariff)
        await session.flush()
        return tariff

    @staticmethod
    async def select_origin_node(session: AsyncSession) -> Server:
        stmt = select(Server).where(Server.health_state.in_([ServerHealthState.ONLINE, ServerHealthState.WAITING_CONFIRMATION])).order_by(Server.id.asc())
        for server in (await session.execute(stmt)).scalars():
            if "xray_origin" in (server.capabilities or []):
                return server
        raise RuntimeError("No healthy server with xray_origin capability is available.")

    @staticmethod
    def _new_quote(*, user_id: int, operation_type: str, target_version_id: int, amount_due: Decimal, expires_at, resulting_paid_hours: int = 0, resulting_paid_value: Decimal = Decimal("0"), source_version_id: int | None = None) -> TariffQuote:
        return TariffQuote(
            public_id=uuid.uuid4(), user_id=user_id, service_type=WHITE_INTERNET_SERVICE_TYPE,
            operation_type=operation_type, source_tariff_version_id=source_version_id, target_tariff_version_id=target_version_id,
            current_paid_hours=0, current_paid_value_rub=Decimal("0"), bonus_hours=0, amount_due_rub=amount_due,
            resulting_paid_hours=resulting_paid_hours, resulting_paid_value_rub=resulting_paid_value, resulting_bonus_hours=0,
            rounding_loss_hours=Decimal("0"), rounding_loss_value_rub=Decimal("0"), currency="RUB",
            status=TariffQuoteStatus.ACTIVE, expires_at=expires_at,
        )

    @classmethod
    async def purchase_subscription(cls, session: AsyncSession, user_id: int):
        user = await lock_checkout_user(session, user_id)
        if user is None:
            return False, texts.WL_USER_NOT_FOUND, None
        existing = await white_internet_repo.get_subscription_by_user_id(session, user_id)
        now = now_utc()
        if existing is not None:
            if existing.status == WhiteInternetStatus.DISABLED:
                return False, texts.WL_SUB_DISABLED, existing
            if existing.expires_at <= now and existing.status in (WhiteInternetStatus.PENDING, WhiteInternetStatus.ACTIVE, WhiteInternetStatus.EXHAUSTED):
                await white_internet_repo.expire_subscription_atomic(session, existing.id)
            elif existing.status in (WhiteInternetStatus.PENDING, WhiteInternetStatus.ACTIVE, WhiteInternetStatus.EXHAUSTED):
                return False, texts.WL_ALREADY_ACTIVE, existing

        tariff = await cls.get_or_create_white_internet_tariff(session)
        tariff_version = await get_or_create_current_version(session, tariff)
        origin_node = await cls.select_origin_node(session)
        quote = cls._new_quote(user_id=user.id, operation_type=TariffQuoteOperation.PURCHASE, target_version_id=tariff_version.id, amount_due=Decimal(tariff.price_rub), expires_at=now + timedelta(minutes=15), resulting_paid_hours=tariff_version.duration_hours, resulting_paid_value=Decimal(tariff_version.price_rub))
        session.add(quote)
        await session.flush()
        try:
            await create_purchase_debit(session, user_id=user.id, quote_id=quote.id, amount=quote.amount_due_rub)
        except InsufficientAccountBalanceError:
            quote.status = TariffQuoteStatus.CANCELLED
            await session.flush()
            return False, texts.WL_INSUFFICIENT_BALANCE_BUY.format(price=int(tariff.price_rub), balance=user.balance_rub, shortage=Decimal(tariff.price_rub) - user.balance_rub), None
        except AccountLedgerError as exc:
            quote.status = TariffQuoteStatus.CANCELLED
            await session.flush()
            return False, f"{texts.WL_DEBIT_FAILED}: {exc}", None
        quote.status = TariffQuoteStatus.CONSUMED
        quote.consumed_at = now_utc()
        sub = await white_internet_repo.create_white_internet_subscription(session, user_id=user.id, origin_node_id=origin_node.id, token=secrets.token_hex(32), uuid=str(uuid.uuid4()), quote_id=quote.id, price_rub=Decimal(tariff.price_rub), duration_days=tariff.duration_days, base_bytes=WHITE_INTERNET_BASE_TRAFFIC_BYTES)
        return True, texts.WL_BUY_SUCCESS, sub

    @classmethod
    async def renew_subscription(cls, session: AsyncSession, user_id: int):
        user = await lock_checkout_user(session, user_id)
        if user is None:
            return False, texts.WL_USER_NOT_FOUND, None
        sub = await white_internet_repo.get_subscription_by_user_id(session, user_id)
        if sub is None:
            return False, texts.WL_SUB_NOT_FOUND, None
        if sub.status == WhiteInternetStatus.DISABLED:
            return False, texts.WL_SUB_DISABLED, None
        if sub.status == WhiteInternetStatus.PENDING:
            return False, texts.WL_SUB_NOT_READY, None
        tariff = await cls.get_or_create_white_internet_tariff(session)
        tariff_version = await get_or_create_current_version(session, tariff)
        now = now_utc()
        quote = cls._new_quote(user_id=user.id, operation_type=TariffQuoteOperation.RENEW, target_version_id=tariff_version.id, source_version_id=tariff_version.id, amount_due=Decimal(tariff.price_rub), expires_at=now + timedelta(minutes=15), resulting_paid_hours=tariff_version.duration_hours, resulting_paid_value=Decimal(tariff_version.price_rub))
        session.add(quote)
        await session.flush()
        try:
            await create_purchase_debit(session, user_id=user.id, quote_id=quote.id, amount=quote.amount_due_rub)
        except InsufficientAccountBalanceError:
            quote.status = TariffQuoteStatus.CANCELLED
            await session.flush()
            return False, texts.WL_INSUFFICIENT_BALANCE_RENEW.format(price=int(tariff.price_rub), balance=user.balance_rub, shortage=Decimal(tariff.price_rub) - user.balance_rub), None
        except AccountLedgerError as exc:
            quote.status = TariffQuoteStatus.CANCELLED
            await session.flush()
            return False, f"{texts.WL_DEBIT_FAILED}: {exc}", None
        quote.status = TariffQuoteStatus.CONSUMED
        quote.consumed_at = now_utc()
        renewed = await white_internet_repo.renew_subscription_atomic(session, subscription_id=sub.id, quote_id=quote.id, price_rub=Decimal(tariff.price_rub), duration_days=tariff.duration_days, base_bytes=WHITE_INTERNET_BASE_TRAFFIC_BYTES)
        return True, texts.WL_RENEW_SUCCESS, renewed

    @classmethod
    async def topup_quota(cls, session: AsyncSession, user_id: int, pack_gb: int):
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
        if sub.status in (WhiteInternetStatus.PENDING, WhiteInternetStatus.DISABLED):
            return False, texts.WL_SUB_NOT_READY, None
        if sub.status == WhiteInternetStatus.EXPIRED or sub.expires_at <= now:
            return False, texts.WL_SUB_EXPIRED, None
        current_available = await white_internet_repo.get_available_quota_bytes(session, sub.id, now)
        pack_bytes = pack_gb * 1024 * 1024 * 1024
        if current_available + pack_bytes > WHITE_INTERNET_MAX_QUOTA_BYTES:
            return False, texts.WL_CAP_EXCEEDED.format(gb=pack_gb, available=current_available // (1024**3)), None
        tariff = await cls.get_or_create_white_internet_tariff(session)
        tariff_version = await get_or_create_current_version(session, tariff)
        quote = cls._new_quote(user_id=user.id, operation_type=TariffQuoteOperation.PURCHASE, target_version_id=tariff_version.id, amount_due=pack_price, expires_at=now + timedelta(minutes=15))
        session.add(quote)
        await session.flush()
        try:
            await create_purchase_debit(session, user_id=user.id, quote_id=quote.id, amount=quote.amount_due_rub)
        except InsufficientAccountBalanceError:
            quote.status = TariffQuoteStatus.CANCELLED
            await session.flush()
            return False, texts.WL_INSUFFICIENT_BALANCE_TOPUP.format(gb=pack_gb, price=int(pack_price), balance=user.balance_rub, shortage=pack_price - user.balance_rub), None
        except AccountLedgerError as exc:
            quote.status = TariffQuoteStatus.CANCELLED
            await session.flush()
            return False, f"{texts.WL_DEBIT_FAILED}: {exc}", None
        quote.status = TariffQuoteStatus.CONSUMED
        quote.consumed_at = now_utc()
        grant = await white_internet_repo.topup_quota_atomic(session, subscription_id=sub.id, quote_id=quote.id, pack_gb=pack_gb, price_rub=pack_price)
        return True, texts.WL_TOPUP_SUCCESS.format(gb=pack_gb), grant

    @staticmethod
    def generate_vless_links(subscription: WhiteInternetSubscription, cdn_domain: str, port: int = 443) -> list[str]:
        extra_dict = {"mode": "packet-up", "uplinkHTTPMethod": "OPTIONS", "xPaddingObfsMode": True, "xPaddingKey": "dc", "xPaddingHeader": "X-Cache", "xPaddingMethod": "tokenish", "xPaddingPlacement": "queryInHeader"}
        extra_param = urllib.parse.quote(json.dumps(extra_dict, separators=(",", ":")))
        tag_de = urllib.parse.quote(texts.WL_VLESS_TAG_DE)
        tag_nl = urllib.parse.quote(texts.WL_VLESS_TAG_NL)
        def build(path: str, tag: str) -> str:
            return f"vless://{subscription.uuid}@{cdn_domain}:{port}?encryption=none&security=tls&sni={cdn_domain}&type=xhttp&path={urllib.parse.quote(path, safe='')}&mode=packet-up&extra={extra_param}#{tag}"
        return [build("/api/v3/de", tag_de), build("/api/v3/nl", tag_nl)]
