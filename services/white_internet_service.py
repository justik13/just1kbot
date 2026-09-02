"""Business logic orchestration for White Internet subscriptions, quotas, and client integration."""

from __future__ import annotations

import json
import logging
import secrets
import urllib.parse
import uuid
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from config.constants import (
    DEFAULT_WHITE_INTERNET_PADDING_KEY,
    DEFAULT_WHITE_INTERNET_PATH,
    WHITE_INTERNET_BASE_DURATION_DAYS,
    WHITE_INTERNET_BASE_PRICE_RUB,
    WHITE_INTERNET_BASE_TRAFFIC_BYTES,
    WHITE_INTERNET_MAX_QUOTA_BYTES,
    WHITE_INTERNET_SERVICE_TYPE,
    WHITE_INTERNET_TLS_FINGERPRINT,
    WHITE_INTERNET_TOPUP_PACKS,
)
from config.enums import ServerHealthState, TariffQuoteOperation, TariffQuoteStatus, WhiteInternetStatus
from database.models import Server, Tariff, TariffQuote, WhiteInternetSubscription
from database.repositories import white_internet_repo
from database.repositories.account_ledger_repo import (
    AccountLedgerError,
    InsufficientAccountBalanceError,
    create_purchase_debit,
    get_account_balance,
)
from database.repositories.servers_repo import capacity_consuming_wl_condition
from database.repositories.tariff_quotes_repo import get_or_create_current_version, lock_checkout_user
from utils.datetime_helpers import now_utc

logger = logging.getLogger(__name__)


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
    async def get_or_create_white_internet_tariff(session: AsyncSession, *, price_rub: Decimal = WHITE_INTERNET_BASE_PRICE_RUB, duration_days: int = WHITE_INTERNET_BASE_DURATION_DAYS) -> Tariff:
        stmt = select(Tariff).where(
            Tariff.service_type == WHITE_INTERNET_SERVICE_TYPE,
            Tariff.duration_days == duration_days,
            Tariff.device_limit == 1,
        ).limit(1)
        tariff = (await session.execute(stmt)).scalar_one_or_none()
        if tariff is not None:
            if not tariff.is_active:
                tariff.is_active = True
                await session.flush()
            return tariff
        tariff = Tariff(name=texts.WL_DEFAULT_TARIFF_NAME, service_type=WHITE_INTERNET_SERVICE_TYPE, device_limit=1, duration_days=duration_days, price_rub=int(price_rub), is_active=True, sort_order=100)
        session.add(tariff)
        await session.flush()
        return tariff

    @staticmethod
    async def select_origin_node(session: AsyncSession) -> Server:
        candidate_ids = (
            await session.scalars(
                select(Server.id)
                .where(
                    Server.health_state == ServerHealthState.ONLINE,
                    Server.api_url.is_not(None),
                    Server.api_key.is_not(None),
                    Server.is_active.is_(True),
                )
                .order_by(Server.id.asc())
            )
        ).all()

        for srv_id in candidate_ids:
            server = await session.scalar(
                select(Server)
                .where(Server.id == srv_id)
                .with_for_update()
            )
            if server is None:
                continue
            if (
                "xray_origin" in (server.capabilities or [])
                and server.api_url
                and server.api_url.strip()
                and server.api_key
                and server.api_key.strip()
            ):
                active_count = await session.scalar(
                    select(func.count(WhiteInternetSubscription.id)).where(
                        WhiteInternetSubscription.origin_node_id == server.id,
                        capacity_consuming_wl_condition(),
                    )
                ) or 0
                if active_count < server.max_clients:
                    return server
        raise RuntimeError("No healthy server with xray_origin capability and available capacity is available.")


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
        quote = cls._new_quote(user_id=user.id, operation_type=TariffQuoteOperation.PURCHASE, target_version_id=tariff_version.id, amount_due=Decimal(tariff_version.price_rub), expires_at=now + timedelta(minutes=15), resulting_paid_hours=tariff_version.duration_hours, resulting_paid_value=Decimal(tariff_version.price_rub))
        session.add(quote)
        await session.flush()
        try:
            await create_purchase_debit(session, user_id=user.id, quote_id=quote.id, amount=quote.amount_due_rub)
        except InsufficientAccountBalanceError:
            quote.status = TariffQuoteStatus.CANCELLED
            await session.flush()
            balance_snap = await get_account_balance(session, user_id=user.id)
            return False, texts.WL_INSUFFICIENT_BALANCE_BUY.format(
                price=int(tariff_version.price_rub),
                balance=balance_snap.available,
                shortage=max(Decimal(tariff_version.price_rub) - balance_snap.available, Decimal(0)),
            ), None
        except AccountLedgerError as exc:
            quote.status = TariffQuoteStatus.CANCELLED
            await session.flush()
            return False, f"{texts.WL_DEBIT_FAILED}: {exc}", None

        quote.status = TariffQuoteStatus.CONSUMED
        quote.consumed_at = now_utc()
        sub = await white_internet_repo.create_white_internet_subscription(session, user_id=user.id, origin_node_id=origin_node.id, token=secrets.token_hex(32), uuid=str(uuid.uuid4()), quote_id=quote.id, price_rub=Decimal(tariff_version.price_rub), duration_days=tariff.duration_days, base_bytes=WHITE_INTERNET_BASE_TRAFFIC_BYTES)
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
        quote = cls._new_quote(user_id=user.id, operation_type=TariffQuoteOperation.RENEW, target_version_id=tariff_version.id, source_version_id=tariff_version.id, amount_due=Decimal(tariff_version.price_rub), expires_at=now + timedelta(minutes=15), resulting_paid_hours=tariff_version.duration_hours, resulting_paid_value=Decimal(tariff_version.price_rub))
        session.add(quote)
        await session.flush()
        try:
            await create_purchase_debit(session, user_id=user.id, quote_id=quote.id, amount=quote.amount_due_rub)
        except InsufficientAccountBalanceError:
            quote.status = TariffQuoteStatus.CANCELLED
            await session.flush()
            balance_snap = await get_account_balance(session, user_id=user.id)
            return False, texts.WL_INSUFFICIENT_BALANCE_RENEW.format(
                price=int(tariff_version.price_rub),
                balance=balance_snap.available,
                shortage=max(Decimal(tariff_version.price_rub) - balance_snap.available, Decimal(0)),
            ), None
        except AccountLedgerError as exc:
            quote.status = TariffQuoteStatus.CANCELLED
            await session.flush()
            return False, f"{texts.WL_DEBIT_FAILED}: {exc}", None
        quote.status = TariffQuoteStatus.CONSUMED
        quote.consumed_at = now_utc()
        renewed = await white_internet_repo.renew_subscription_atomic(session, subscription_id=sub.id, quote_id=quote.id, price_rub=Decimal(tariff_version.price_rub), duration_days=tariff.duration_days, base_bytes=WHITE_INTERNET_BASE_TRAFFIC_BYTES)
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
            balance_snap = await get_account_balance(session, user_id=user.id)
            return False, texts.WL_INSUFFICIENT_BALANCE_TOPUP.format(
                gb=pack_gb,
                price=int(pack_price),
                balance=balance_snap.available,
                shortage=max(pack_price - balance_snap.available, Decimal(0)),
            ), None
        except AccountLedgerError as exc:
            quote.status = TariffQuoteStatus.CANCELLED
            await session.flush()
            return False, f"{texts.WL_DEBIT_FAILED}: {exc}", None
        grant = await white_internet_repo.topup_quota_atomic(session, subscription_id=sub.id, quote_id=quote.id, pack_gb=pack_gb, price_rub=pack_price)
        quote.status = TariffQuoteStatus.CONSUMED
        quote.consumed_at = now_utc()
        return True, texts.WL_TOPUP_SUCCESS.format(gb=pack_gb), grant

    @staticmethod
    def generate_vless_links(
        subscription: WhiteInternetSubscription,
        cdn_domain: str,
        port: int = 443,
        path: str = DEFAULT_WHITE_INTERNET_PATH,
        relays: list[dict] | None = None,
    ) -> list[str]:
        extra_dict = {
            "mode": "packet-up",
            "uplinkHTTPMethod": "OPTIONS",
            "xPaddingObfsMode": True,
            "xPaddingKey": DEFAULT_WHITE_INTERNET_PADDING_KEY,
            "xPaddingHeader": "X-Cache",
            "xPaddingMethod": "tokenish",
            "xPaddingPlacement": "queryInHeader",
        }
        extra_param = urllib.parse.quote(json.dumps(extra_dict, separators=(",", ":")))
        base = _normalize_base_path(path)
        if not relays:
            tag = urllib.parse.quote(texts.WL_VLESS_TAG)
            standalone_path = f"{base}/default"
            link = f"vless://{subscription.uuid}@{cdn_domain}:{port}?encryption=none&security=tls&sni={cdn_domain}&alpn=h2&fp={WHITE_INTERNET_TLS_FINGERPRINT}&type=xhttp&path={urllib.parse.quote(standalone_path, safe='')}&mode=packet-up&extra={extra_param}#{tag}"
            return [link]

        links: list[str] = []
        for r in relays:
            r_path = r.get("path") or f"{base}/{r.get('code', 'de')}"
            r_tag = urllib.parse.quote(r.get("name") or texts.WL_VLESS_TAG)
            link = f"vless://{subscription.uuid}@{cdn_domain}:{port}?encryption=none&security=tls&sni={cdn_domain}&alpn=h2&fp={WHITE_INTERNET_TLS_FINGERPRINT}&type=xhttp&path={urllib.parse.quote(r_path, safe='')}&mode=packet-up&extra={extra_param}#{r_tag}"
            links.append(link)
        return links

    @staticmethod
    def generate_full_xray_config(subscription: WhiteInternetSubscription, cdn_domain: str, port: int = 443, path: str = DEFAULT_WHITE_INTERNET_PATH) -> dict:
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
                    "sniffing": {"enabled": True, "destOverride": ["http", "tls"]},
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
                        "security": "tls",
                        "tlsSettings": {
                            "serverName": cdn_domain,
                            "alpn": ["h2"],
                            "fingerprint": WHITE_INTERNET_TLS_FINGERPRINT,
                        },
                        "xhttpSettings": {
                            "path": f"{base}/default",
                            "mode": "packet-up",
                            "uplinkHTTPMethod": "OPTIONS",
                            "xPaddingObfsMode": True,
                            "xPaddingKey": DEFAULT_WHITE_INTERNET_PADDING_KEY,
                            "xPaddingHeader": "X-Cache",
                            "xPaddingMethod": "tokenish",
                            "xPaddingPlacement": "queryInHeader",
                        },
                    },
                },
                {"tag": "direct", "protocol": "freedom"},
                {"tag": "block", "protocol": "blackhole"},
            ],
            "dns": {
                "servers": [
                    "https://1.1.1.1/dns-query",
                    "https://77.88.8.8/dns-query",
                    "localhost",
                ]
            },
            "routing": {
                "domainStrategy": "IPIfNonMatch",
                "rules": [
                    {
                        "type": "field",
                        "ip": ["geoip:private", "geoip:ru"],
                        "outboundTag": "direct",
                    },
                    {
                        "type": "field",
                        "domain": ["geosite:category-ru", "geosite:tld-ru"],
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

