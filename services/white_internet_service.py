"""Business logic orchestration for White Internet subscriptions, quotas, and client integration."""

from __future__ import annotations

import asyncio
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
    CANONICAL_XHTTP_PROFILE,
    DEFAULT_WHITE_INTERNET_PATH,
    WHITE_INTERNET_BASE_DURATION_DAYS,
    WHITE_INTERNET_BASE_PRICE_RUB,
    WHITE_INTERNET_MAX_QUOTA_BYTES,
    WHITE_INTERNET_SERVICE_TYPE,
    WHITE_INTERNET_TLS_FINGERPRINT,
    WHITE_INTERNET_TOPUP_PACKS,
    XRAY_PROTOCOL,
)
from config.enums import (
    ServerHealthState,
    ServerLifecycleStatus,
    TariffQuoteOperation,
    TariffQuoteStatus,
    WhiteInternetProvisioningStatus,
    WhiteInternetStatus,
)
from database.models import Server, Tariff, TariffQuote, WhiteInternetSubscription
from database.repositories import servers_repo, white_internet_repo
from database.repositories.account_ledger_repo import (
    AccountLedgerError,
    InsufficientAccountBalanceError,
    create_purchase_debit,
    get_account_balance,
)
from database.repositories.tariff_quotes_repo import (
    get_or_create_current_version,
    lock_checkout_user,
)
from services.xray_node_client import XrayNodeClient, _sanitize_url
from utils.datetime_helpers import now_utc

logger = logging.getLogger(__name__)

_BACKGROUND_TASKS: set[asyncio.Task] = set()


async def _deprovision_old_node_safe(
    api_url: str, api_key: str, client_uuid: str, version: int
) -> None:
    try:
        async with XrayNodeClient() as client:
            await client.sync_client(
                api_url,
                api_key,
                client_uuid=client_uuid,
                is_active=False,
                version=version,
            )
    except Exception as exc:
        logger.warning("Failed to deprovision old origin node %s: %s", _sanitize_url(api_url), exc)


def _normalize_base_path(path: str | None) -> str:
    cleaned = (path or "").strip().rstrip("/")
    if cleaned.endswith("/default"):
        cleaned = cleaned[:-8].rstrip("/")
    if not cleaned:
        cleaned = DEFAULT_WHITE_INTERNET_PATH.rstrip("/")
    if not cleaned.startswith("/"):
        cleaned = "/" + cleaned
    return cleaned


class WhiteInternetService:
    """Service managing the White Internet product lifecycle."""

    @staticmethod
    async def get_or_create_white_internet_tariff(
        session: AsyncSession,
        *,
        price_rub: Decimal = WHITE_INTERNET_BASE_PRICE_RUB,
        duration_days: int = WHITE_INTERNET_BASE_DURATION_DAYS,
    ) -> Tariff:
        stmt = (
            select(Tariff)
            .where(
                Tariff.service_type == WHITE_INTERNET_SERVICE_TYPE,
                Tariff.duration_days == duration_days,
                Tariff.device_limit == 1,
            )
            .limit(1)
        )
        res = await session.execute(stmt)
        tariff = res.scalar_one_or_none()
        if tariff is not None:
            if not tariff.is_active:
                tariff.is_active = True
                await session.flush()
            return tariff
        tariff = Tariff(
            name=texts.WL_DEFAULT_TARIFF_NAME,
            service_type=WHITE_INTERNET_SERVICE_TYPE,
            device_limit=1,
            duration_days=duration_days,
            price_rub=int(price_rub),
            is_active=True,
            sort_order=100,
        )
        session.add(tariff)
        await session.flush()
        return tariff

    @staticmethod
    async def select_origin_node(session: AsyncSession) -> Server:
        server = await servers_repo.allocate_origin_server_atomic(session)
        if server is None:
            raise RuntimeError(
                "No healthy server with xray_origin capability and available capacity is available."
            )
        return server

    @staticmethod
    def _new_quote(
        *,
        user_id: int,
        operation_type: str,
        target_version_id: int,
        amount_due: Decimal,
        expires_at,
        resulting_paid_hours: int = 0,
        resulting_paid_value: Decimal = Decimal("0"),
        source_version_id: int | None = None,
    ) -> TariffQuote:
        return TariffQuote(
            public_id=uuid.uuid4(),
            user_id=user_id,
            service_type=WHITE_INTERNET_SERVICE_TYPE,
            operation_type=operation_type,
            source_tariff_version_id=source_version_id,
            target_tariff_version_id=target_version_id,
            current_paid_hours=0,
            current_paid_value_rub=Decimal("0"),
            bonus_hours=0,
            amount_due_rub=amount_due,
            resulting_paid_hours=resulting_paid_hours,
            resulting_paid_value_rub=resulting_paid_value,
            resulting_bonus_hours=0,
            rounding_loss_hours=Decimal("0"),
            rounding_loss_value_rub=Decimal("0"),
            currency="RUB",
            status=TariffQuoteStatus.ACTIVE,
            expires_at=expires_at,
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
            if existing.expires_at <= now and existing.status in (
                WhiteInternetStatus.PENDING,
                WhiteInternetStatus.ACTIVE,
                WhiteInternetStatus.EXHAUSTED,
            ):
                await white_internet_repo.expire_subscription_atomic(session, existing.id)
            elif existing.status in (
                WhiteInternetStatus.PENDING,
                WhiteInternetStatus.ACTIVE,
                WhiteInternetStatus.EXHAUSTED,
            ):
                return False, texts.WL_ALREADY_ACTIVE, existing

        tariff = await cls.get_or_create_white_internet_tariff(session)
        tariff_version = await get_or_create_current_version(session, tariff)

        # Pre-Debit Validation: verify mandatory tariff quota BEFORE touching ledger
        if not tariff_version.base_quota_bytes or tariff_version.base_quota_bytes <= 0:
            raise ValueError(
                f"Tariff version {tariff_version.id} missing mandatory immutable base_quota_bytes"
            )

        try:
            origin_node = await cls.select_origin_node(session)
        except RuntimeError as exc:
            logger.warning("No available origin node for white internet purchase: %s", exc)
            return False, texts.WL_NO_SERVERS_AVAILABLE, None
        quote = cls._new_quote(
            user_id=user.id,
            operation_type=TariffQuoteOperation.PURCHASE,
            target_version_id=tariff_version.id,
            amount_due=Decimal(tariff_version.price_rub),
            expires_at=now + timedelta(minutes=15),
            resulting_paid_hours=tariff_version.duration_hours,
            resulting_paid_value=Decimal(tariff_version.price_rub),
        )
        session.add(quote)
        await session.flush()
        try:
            await create_purchase_debit(
                session, user_id=user.id, quote_id=quote.id, amount=quote.amount_due_rub
            )
        except InsufficientAccountBalanceError:
            quote.status = TariffQuoteStatus.CANCELLED
            await session.flush()
            balance_snap = await get_account_balance(session, user_id=user.id)
            return (
                False,
                texts.WL_INSUFFICIENT_BALANCE_BUY.format(
                    price=int(tariff_version.price_rub),
                    balance=balance_snap.available,
                    shortage=max(
                        Decimal(tariff_version.price_rub) - balance_snap.available, Decimal(0)
                    ),
                ),
                None,
            )
        except AccountLedgerError as exc:
            quote.status = TariffQuoteStatus.CANCELLED
            await session.flush()
            return False, f"{texts.WL_DEBIT_FAILED}: {exc}", None

        quote.status = TariffQuoteStatus.CONSUMED

        sub = await white_internet_repo.create_white_internet_subscription(
            session,
            user_id=user.id,
            origin_node_id=origin_node.id,
            token=secrets.token_hex(32),
            uuid=str(uuid.uuid4()),
            quote_id=quote.id,
            price_rub=Decimal(tariff_version.price_rub),
            duration_days=tariff.duration_days,
            base_bytes=tariff_version.base_quota_bytes,
        )
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

        # Validate origin node health & availability before debiting funds
        origin_node = await session.scalar(
            select(Server).where(
                Server.id == sub.origin_node_id,
                Server.protocol == XRAY_PROTOCOL,
            )
        )
        needs_migration = (
            not origin_node
            or not origin_node.is_active
            or origin_node.health_state != ServerHealthState.ONLINE
            or origin_node.lifecycle_status != ServerLifecycleStatus.ACTIVE
            or not (origin_node.extra_data or {}).get("relays")
        )
        new_origin_server: Server | None = None
        if needs_migration:
            try:
                new_origin_server = await cls.select_origin_node(session)
            except RuntimeError as exc:
                logger.warning("No healthy origin node available for renewal migration: %s", exc)
                return False, texts.WL_NO_SERVERS_AVAILABLE, None

        tariff = await cls.get_or_create_white_internet_tariff(session)
        tariff_version = await get_or_create_current_version(session, tariff)

        # Pre-Debit Validation: verify mandatory tariff quota BEFORE touching ledger
        if not tariff_version.base_quota_bytes or tariff_version.base_quota_bytes <= 0:
            raise ValueError(
                f"Tariff version {tariff_version.id} missing mandatory immutable base_quota_bytes"
            )

        now = now_utc()
        quote = cls._new_quote(
            user_id=user.id,
            operation_type=TariffQuoteOperation.RENEW,
            target_version_id=tariff_version.id,
            source_version_id=tariff_version.id,
            amount_due=Decimal(tariff_version.price_rub),
            expires_at=now + timedelta(minutes=15),
            resulting_paid_hours=tariff_version.duration_hours,
            resulting_paid_value=Decimal(tariff_version.price_rub),
        )
        session.add(quote)
        await session.flush()
        try:
            await create_purchase_debit(
                session, user_id=user.id, quote_id=quote.id, amount=quote.amount_due_rub
            )
        except InsufficientAccountBalanceError:
            quote.status = TariffQuoteStatus.CANCELLED
            await session.flush()
            balance_snap = await get_account_balance(session, user_id=user.id)
            return (
                False,
                texts.WL_INSUFFICIENT_BALANCE_RENEW.format(
                    price=int(tariff_version.price_rub),
                    balance=balance_snap.available,
                    shortage=max(
                        Decimal(tariff_version.price_rub) - balance_snap.available, Decimal(0)
                    ),
                ),
                None,
            )
        except AccountLedgerError as exc:
            quote.status = TariffQuoteStatus.CANCELLED
            await session.flush()
            return False, f"{texts.WL_DEBIT_FAILED}: {exc}", None
        quote.status = TariffQuoteStatus.CONSUMED
        quote.consumed_at = now_utc()

        # Apply node migration ONLY after successful financial debit
        old_origin_for_cleanup: Server | None = None
        if needs_migration and new_origin_server is not None:
            old_origin_for_cleanup = origin_node
            sub.origin_node_id = new_origin_server.id
            sub.actual_version = 0
            sub.last_reconciled_node_epoch = None
            sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_CREATE
            await session.flush()

        renewed = await white_internet_repo.renew_subscription_atomic(
            session,
            subscription_id=sub.id,
            quote_id=quote.id,
            price_rub=Decimal(tariff_version.price_rub),
            duration_days=tariff.duration_days,
            base_bytes=tariff_version.base_quota_bytes,
        )

        # Best-effort deprovisioning of UUID on old origin node to prevent orphaned credentials
        if (
            old_origin_for_cleanup
            and old_origin_for_cleanup.api_url
            and old_origin_for_cleanup.api_key
        ):
            try:
                task = asyncio.create_task(
                    _deprovision_old_node_safe(
                        old_origin_for_cleanup.api_url,
                        old_origin_for_cleanup.api_key,
                        client_uuid=sub.uuid,
                        version=sub.desired_version + 1,
                    )
                )
                _BACKGROUND_TASKS.add(task)
                task.add_done_callback(_BACKGROUND_TASKS.discard)
            except Exception as deprov_err:
                logger.warning(
                    "Failed to dispatch async deprovision on old origin %s: %s",
                    old_origin_for_cleanup.id,
                    deprov_err,
                )

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

        pack_bytes = pack_gb * 1024 * 1024 * 1024
        total_accumulated = (
            (sub.base_traffic_bytes or 0) + (sub.extra_traffic_bytes or 0) + pack_bytes
        )
        if total_accumulated > WHITE_INTERNET_MAX_QUOTA_BYTES:
            current_available = await white_internet_repo.get_available_quota_bytes(
                session, sub.id, now
            )
            return (
                False,
                texts.WL_CAP_EXCEEDED.format(gb=pack_gb, available=current_available // (1024**3)),
                None,
            )

        tariff = await cls.get_or_create_white_internet_tariff(session)
        tariff_version = await get_or_create_current_version(session, tariff)
        quote = cls._new_quote(
            user_id=user.id,
            operation_type=TariffQuoteOperation.PURCHASE,
            target_version_id=tariff_version.id,
            amount_due=pack_price,
            expires_at=now + timedelta(minutes=15),
        )
        session.add(quote)
        await session.flush()
        try:
            await create_purchase_debit(
                session, user_id=user.id, quote_id=quote.id, amount=quote.amount_due_rub
            )
        except InsufficientAccountBalanceError:
            quote.status = TariffQuoteStatus.CANCELLED
            await session.flush()
            balance_snap = await get_account_balance(session, user_id=user.id)
            return (
                False,
                texts.WL_INSUFFICIENT_BALANCE_TOPUP.format(
                    gb=pack_gb,
                    price=int(pack_price),
                    balance=balance_snap.available,
                    shortage=max(pack_price - balance_snap.available, Decimal(0)),
                ),
                None,
            )
        except AccountLedgerError as exc:
            quote.status = TariffQuoteStatus.CANCELLED
            await session.flush()
            return False, f"{texts.WL_DEBIT_FAILED}: {exc}", None
        grant = await white_internet_repo.topup_quota_atomic(
            session,
            subscription_id=sub.id,
            quote_id=quote.id,
            pack_gb=pack_gb,
            price_rub=pack_price,
        )
        quote.status = TariffQuoteStatus.CONSUMED
        quote.consumed_at = now_utc()
        return True, texts.WL_TOPUP_SUCCESS.format(gb=pack_gb), grant

    @classmethod
    async def create_trial_subscription(cls, session: AsyncSession, user_id: int):
        """
        # TODO(trial): Trial period implementation (1-3 days, 5-10 GiB, 0 RUB)
        # To be enabled in UI after primary White Internet rollout stabilization.
        """
        raise NotImplementedError("Trial period feature is planned for a future release.")

    @staticmethod
    def generate_vless_links(
        subscription: WhiteInternetSubscription,
        cdn_domain: str,
        port: int = 443,
        path: str = DEFAULT_WHITE_INTERNET_PATH,
        relays: list[dict] | None = None,
    ) -> list[str]:
        extra_dict = {
            "mode": CANONICAL_XHTTP_PROFILE["mode"],
            "uplinkHTTPMethod": CANONICAL_XHTTP_PROFILE["uplinkHTTPMethod"],
            "xPaddingObfsMode": CANONICAL_XHTTP_PROFILE["xPaddingObfsMode"],
            "xPaddingKey": CANONICAL_XHTTP_PROFILE["xPaddingKey"],
            "xPaddingHeader": CANONICAL_XHTTP_PROFILE["xPaddingHeader"],
            "xPaddingMethod": CANONICAL_XHTTP_PROFILE["xPaddingMethod"],
            "xPaddingPlacement": CANONICAL_XHTTP_PROFILE["xPaddingPlacement"],
        }
        extra_param = urllib.parse.quote(json.dumps(extra_dict, separators=(",", ":")))
        fp = CANONICAL_XHTTP_PROFILE.get("fp", WHITE_INTERNET_TLS_FINGERPRINT)
        base = _normalize_base_path(path)
        if not relays:
            tag = urllib.parse.quote(texts.WL_VLESS_TAG)
            standalone_path = f"{base}/default"
            link = f"vless://{subscription.uuid}@{cdn_domain}:{port}?encryption=none&security=tls&sni={cdn_domain}&alpn=h2&fp={fp}&type=xhttp&path={urllib.parse.quote(standalone_path, safe='')}&mode=packet-up&extra={extra_param}#{tag}"
            return [link]

        links: list[str] = []
        for r in relays:
            relay_code = r.get("code") or r.get("name") or "default"
            r_path = r.get("path") or f"{base}/{relay_code}"
            r_tag = urllib.parse.quote(r.get("name") or texts.WL_VLESS_TAG)
            link = f"vless://{subscription.uuid}@{cdn_domain}:{port}?encryption=none&security=tls&sni={cdn_domain}&alpn=h2&fp={fp}&type=xhttp&path={urllib.parse.quote(r_path, safe='')}&mode=packet-up&extra={extra_param}#{r_tag}"
            links.append(link)
        return links

    @staticmethod
    def generate_full_xray_config(
        subscription: WhiteInternetSubscription,
        cdn_domain: str,
        port: int = 443,
        path: str = DEFAULT_WHITE_INTERNET_PATH,
    ) -> dict:
        """Generate complete Xray client JSON config for INCY / Happ / v2rayN."""
        base = _normalize_base_path(path)
        return {
            "log": {"loglevel": "warning"},
            "inbounds": [
                {
                    "tag": "socks-in",
                    "port": 10808,
                    "listen": "127.0.0.1",
                    "protocol": "socks",
                    "settings": {"auth": "noauth", "udp": True},
                    "sniffing": {"enabled": True, "destOverride": ["http", "tls", "fakedns"]},
                }
            ],
            "outbounds": [
                {
                    "tag": "proxy-white-internet",
                    "protocol": "vless",
                    "settings": {
                        "vnext": [
                            {
                                "address": cdn_domain,
                                "port": port,
                                "users": [
                                    {
                                        "id": subscription.uuid,
                                        "encryption": "none",
                                    }
                                ],
                            }
                        ]
                    },
                    "streamSettings": {
                        "network": "xhttp",
                        "security": CANONICAL_XHTTP_PROFILE["security"],
                        "tlsSettings": {
                            "serverName": cdn_domain,
                            "alpn": CANONICAL_XHTTP_PROFILE["alpn"],
                            "fingerprint": CANONICAL_XHTTP_PROFILE.get(
                                "fp", WHITE_INTERNET_TLS_FINGERPRINT
                            ),
                        },
                        "xhttpSettings": {
                            "path": f"{base}/default",
                            "mode": CANONICAL_XHTTP_PROFILE["mode"],
                            "uplinkHTTPMethod": CANONICAL_XHTTP_PROFILE["uplinkHTTPMethod"],
                            "xPaddingObfsMode": CANONICAL_XHTTP_PROFILE["xPaddingObfsMode"],
                            "xPaddingKey": CANONICAL_XHTTP_PROFILE["xPaddingKey"],
                            "xPaddingHeader": CANONICAL_XHTTP_PROFILE["xPaddingHeader"],
                            "xPaddingMethod": CANONICAL_XHTTP_PROFILE["xPaddingMethod"],
                            "xPaddingPlacement": CANONICAL_XHTTP_PROFILE["xPaddingPlacement"],
                        },
                    },
                },
                {"tag": "direct", "protocol": "freedom"},
                {"tag": "block", "protocol": "blackhole"},
            ],
            "dns": {
                "hosts": {
                    "cloudflare-dns.com": "1.1.1.1",
                    "dns.google": "8.8.8.8",
                },
                "servers": [
                    "fakedns",
                    "https://1.1.1.1/dns-query",
                    "https://dns.google/dns-query",
                ],
            },
            "fakedns": [
                {
                    "ipPool": "198.18.0.0/15",
                    "poolSize": 32768,
                }
            ],
            "routing": {
                "domainStrategy": "IPIfNonMatch",
                "rules": [
                    {
                        "type": "field",
                        "ip": ["1.1.1.1", "8.8.8.8"],
                        "outboundTag": "proxy-white-internet",
                    },
                    {
                        "type": "field",
                        "ip": ["geoip:private"],
                        "outboundTag": "direct",
                    },
                    {
                        "type": "field",
                        "port": "0-65535",
                        "outboundTag": "proxy-white-internet",
                    },
                ],
            },
        }
