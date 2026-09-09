"""Business logic orchestration for White Internet subscriptions, quotas, and client integration."""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import urllib.parse
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from config.constants import (
    CANONICAL_XHTTP_PROFILE,
    DEFAULT_WHITE_INTERNET_PATH,
    WHITE_INTERNET_BASE_DURATION_DAYS,
    WHITE_INTERNET_BASE_PRICE_RUB,
    WHITE_INTERNET_EXTRA_DEVICE_PRICE_RUB,
    WHITE_INTERNET_EXTRA_DEVICE_TRAFFIC_BYTES,
    WHITE_INTERNET_MAX_DEVICE_LIMIT,
    WHITE_INTERNET_MAX_EXPIRY_DAYS,
    WHITE_INTERNET_MAX_QUOTA_BYTES,
    WHITE_INTERNET_SERVICE_TYPE,
    WHITE_INTERNET_TLS_FINGERPRINT,
    WHITE_INTERNET_TOPUP_PACKS,
    WHITE_INTERNET_TRIAL_DURATION_DAYS,
    WHITE_INTERNET_TRIAL_TRAFFIC_BYTES,
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
from services.xray_node_client import SyncResult, XrayNodeClient, _sanitize_url
from utils.admin import is_admin
from utils.datetime_helpers import now_utc

logger = logging.getLogger(__name__)

_BACKGROUND_TASKS: set[asyncio.Task] = set()


def get_white_internet_tier_price(device_limit: int, base_price: Decimal | None = None) -> Decimal:
    """Calculate White Internet monthly renewal/subscription price based on device slots.

    1 device  = base_price (50 GiB base)
    2 devices = base_price + 200 RUB (100 GiB base)
    3 devices = base_price + 400 RUB (150 GiB base)
    """
    limit = max(1, min(int(device_limit), WHITE_INTERNET_MAX_DEVICE_LIMIT))
    base = base_price if base_price is not None else WHITE_INTERNET_BASE_PRICE_RUB
    return base + Decimal(limit - 1) * WHITE_INTERNET_EXTRA_DEVICE_PRICE_RUB


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


def _dispatch_deprovision(
    server: Server | None,
    client_uuid: str,
    version: int,
    *,
    context: str = "",
) -> None:
    if not server or not server.api_url or not server.api_key:
        return
    if getattr(server, "protocol", None) != XRAY_PROTOCOL:
        logger.warning(
            "Refusing cross-protocol deprovision dispatch (%s): server %s is not xray",
            context,
            getattr(server, "id", "?"),
        )
        return
    try:
        task = asyncio.create_task(
            _deprovision_old_node_safe(
                server.api_url,
                server.api_key,
                client_uuid=client_uuid,
                version=version,
            )
        )
        _BACKGROUND_TASKS.add(task)
        task.add_done_callback(_BACKGROUND_TASKS.discard)
    except Exception as exc:
        logger.warning("Failed to schedule deprovision task (%s): %s", context, exc)


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
            purchase_notified_at=now_utc(),
        )

    @classmethod
    async def purchase_subscription(cls, session: AsyncSession, user_id: int):
        user = await lock_checkout_user(session, user_id)
        if user is None:
            return False, texts.WL_USER_NOT_FOUND, None
        existing = await white_internet_repo.get_subscription_by_user_id(session, user_id)
        now = now_utc()
        if existing is not None:
            if getattr(existing, "is_trial", False):
                return await cls.convert_trial_to_paid(session, user_id)
            if existing.status == WhiteInternetStatus.DISABLED:
                return False, texts.WL_SUB_DISABLED, existing
            if (
                getattr(existing, "pending_hard_delete", False)
                or getattr(existing, "provisioning_status", None)
                == WhiteInternetProvisioningStatus.PENDING_DELETE
            ):
                return False, texts.WL_DEACTIVATION_PENDING, existing
            if existing.status in (
                WhiteInternetStatus.PENDING,
                WhiteInternetStatus.ACTIVE,
                WhiteInternetStatus.EXHAUSTED,
            ) and existing.expires_at and existing.expires_at > now:
                return False, texts.WL_ALREADY_ACTIVE, existing
            # Existing paid subscription routes to renew_subscription to preserve UUID and token
            return await cls.renew_subscription(session, user_id)

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
        # Commit DB state before executing external network sync.
        # This durably persists user debit, quote, and subscription in PostgreSQL
        # and releases all SELECT FOR UPDATE row locks (Server, User) so concurrent
        # operations are not blocked during external network I/O.
        # If commit fails, we fail-closed immediately WITHOUT mutating Xray.
        await session.commit()

        await cls._try_inline_sync(
            session, sub, origin_node, idempotency_key=f"purchase:{sub.id}:1:True"
        )
        return True, texts.WL_BUY_SUCCESS, sub

    @classmethod
    async def convert_trial_to_paid(cls, session: AsyncSession, user_id: int):
        """Convert an existing trial subscription to a full paid subscription."""
        user = await lock_checkout_user(session, user_id)
        if user is None:
            return False, texts.WL_USER_NOT_FOUND, None
        sub = await white_internet_repo.get_subscription_by_user_id(session, user_id)
        if sub is None:
            return False, texts.WL_SUB_NOT_FOUND, None
        if not getattr(sub, "is_trial", False):
            return False, texts.WL_ALREADY_ACTIVE, sub
        if sub.status == WhiteInternetStatus.DISABLED:
            return False, texts.WL_SUB_DISABLED, None
        if (
            getattr(sub, "pending_hard_delete", False)
            or getattr(sub, "provisioning_status", None)
            == WhiteInternetProvisioningStatus.PENDING_DELETE
        ):
            return False, texts.WL_DEACTIVATION_PENDING, None

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
                logger.warning("No healthy origin node available for trial conversion migration: %s", exc)
                return False, texts.WL_NO_SERVERS_AVAILABLE, None

        tariff = await cls.get_or_create_white_internet_tariff(session)
        tariff_version = await get_or_create_current_version(session, tariff)

        # Pre-Debit Validation: verify mandatory tariff quota BEFORE touching ledger
        if not tariff_version.base_quota_bytes or tariff_version.base_quota_bytes <= 0:
            raise ValueError(
                f"Tariff version {tariff_version.id} missing mandatory immutable base_quota_bytes"
            )

        now = now_utc()
        sub_device_limit = max(1, getattr(sub, "device_limit", 1) or 1)
        tier_price = get_white_internet_tier_price(sub_device_limit, base_price=Decimal(tariff_version.price_rub))
        tier_base_bytes = sub_device_limit * tariff_version.base_quota_bytes

        quote = cls._new_quote(
            user_id=user.id,
            operation_type=TariffQuoteOperation.PURCHASE,
            target_version_id=tariff_version.id,
            source_version_id=tariff_version.id,
            amount_due=tier_price,
            expires_at=now + timedelta(minutes=15),
            resulting_paid_hours=tariff_version.duration_hours,
            resulting_paid_value=tier_price,
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
                    price=int(tier_price),
                    balance=balance_snap.available,
                    shortage=max(tier_price - balance_snap.available, Decimal(0)),
                ),
                None,
            )
        except AccountLedgerError as exc:
            quote.status = TariffQuoteStatus.CANCELLED
            await session.flush()
            return False, f"{texts.WL_DEBIT_FAILED}: {exc}", None
        quote.status = TariffQuoteStatus.CONSUMED
        quote.consumed_at = now_utc()

        old_origin_for_cleanup: Server | None = None
        if needs_migration and new_origin_server is not None:
            old_origin_for_cleanup = origin_node
            sub.origin_node_id = new_origin_server.id
            sub.actual_version = 0
            sub.last_reconciled_node_epoch = None
            sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_CREATE
            await session.flush()
            await white_internet_repo.cancel_pending_orphan_cleanups_for_client(
                session, new_origin_server.id, sub.uuid
            )

        sub_locked = await white_internet_repo.get_subscription_with_lock(session, sub.id)
        if sub_locked is None:
            return False, texts.WL_SUB_NOT_FOUND, None

        base_time = sub_locked.expires_at if sub_locked.expires_at and sub_locked.expires_at > now else now
        sub_locked.is_trial = False
        sub_locked.base_traffic_bytes = tier_base_bytes
        sub_locked.extra_traffic_bytes = 0
        sub_locked.traffic_used_bytes = 0
        sub_locked.traffic_uplink_bytes = 0
        sub_locked.traffic_downlink_bytes = 0
        sub_locked.traffic_overage_bytes = 0
        sub_locked.expires_at = base_time + timedelta(days=tariff.duration_days)
        sub_locked.status = WhiteInternetStatus.ACTIVE
        sub_locked.status_reason = None
        sub_locked.desired_version += 1
        sub_locked.provisioning_status = WhiteInternetProvisioningStatus.PENDING_UPDATE
        await session.flush()
        await session.refresh(sub_locked)

        if old_origin_for_cleanup is not None and old_origin_for_cleanup.id != sub_locked.origin_node_id:
            await white_internet_repo.enqueue_orphan_cleanup(
                session,
                server_id=old_origin_for_cleanup.id,
                client_uuid=sub_locked.uuid,
                desired_version=sub_locked.desired_version,
            )

        await session.commit()

        active_origin = new_origin_server or origin_node
        if active_origin is not None:
            await cls._try_inline_sync(
                session, sub_locked, active_origin, idempotency_key=f"convert:{sub_locked.id}:{sub_locked.desired_version}:True"
            )

        if old_origin_for_cleanup is not None and old_origin_for_cleanup.id != sub_locked.origin_node_id:
            _dispatch_deprovision(
                old_origin_for_cleanup,
                client_uuid=sub_locked.uuid,
                version=sub_locked.desired_version,
                context=f"trial_convert migration sub {sub_locked.id}",
            )

        return True, texts.WL_BUY_SUCCESS, sub_locked

    @classmethod
    async def renew_subscription(cls, session: AsyncSession, user_id: int):
        user = await lock_checkout_user(session, user_id)
        if user is None:
            return False, texts.WL_USER_NOT_FOUND, None
        sub = await white_internet_repo.get_subscription_by_user_id(session, user_id)
        if sub is None:
            return False, texts.WL_SUB_NOT_FOUND, None
        if getattr(sub, "is_trial", False):
            return await cls.convert_trial_to_paid(session, user_id)
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
        base_time = sub.expires_at if sub.expires_at and sub.expires_at > now else now
        if base_time + timedelta(days=tariff.duration_days) > now + timedelta(
            days=WHITE_INTERNET_MAX_EXPIRY_DAYS
        ):
            return False, texts.WL_RENEWAL_HORIZON_EXCEEDED, None

        sub_device_limit = max(1, getattr(sub, "device_limit", 1) or 1)
        tier_price = get_white_internet_tier_price(sub_device_limit, base_price=Decimal(tariff_version.price_rub))
        tier_base_bytes = sub_device_limit * tariff_version.base_quota_bytes

        quote = cls._new_quote(
            user_id=user.id,
            operation_type=TariffQuoteOperation.RENEW,
            target_version_id=tariff_version.id,
            source_version_id=tariff_version.id,
            amount_due=tier_price,
            expires_at=now + timedelta(minutes=15),
            resulting_paid_hours=tariff_version.duration_hours,
            resulting_paid_value=tier_price,
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
                    price=int(tier_price),
                    balance=balance_snap.available,
                    shortage=max(
                        tier_price - balance_snap.available, Decimal(0)
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
            await white_internet_repo.cancel_pending_orphan_cleanups_for_client(
                session, new_origin_server.id, sub.uuid
            )

        renewed = await white_internet_repo.renew_subscription_atomic(
            session,
            subscription_id=sub.id,
            quote_id=quote.id,
            price_rub=tier_price,
            duration_days=tariff.duration_days,
            base_bytes=tier_base_bytes,
        )

        # Durable deprovisioning of UUID on old origin node: record the cleanup
        # in the same transaction (survives crashes/restarts), then accelerate
        # with a best-effort background dispatch. The reconciliation worker
        # sweeps any row the dispatch misses, so no active credential orphans.
        if old_origin_for_cleanup:
            await white_internet_repo.enqueue_orphan_cleanup(
                session,
                server_id=old_origin_for_cleanup.id,
                client_uuid=sub.uuid,
                desired_version=sub.desired_version + 1,
            )

        await session.flush()

        if old_origin_for_cleanup:
            _dispatch_deprovision(
                old_origin_for_cleanup,
                client_uuid=sub.uuid,
                version=sub.desired_version + 1,
                context=f"renew sub {sub.id}",
            )

        return True, texts.WL_RENEW_SUCCESS, renewed

    @classmethod
    async def purchase_device_slot(
        cls,
        session: AsyncSession,
        user_id: int,
        actor_telegram_id: int | None = None,
    ) -> tuple[bool, str, WhiteInternetSubscription | None]:
        user = await lock_checkout_user(session, user_id)
        if user is None:
            return False, texts.WL_USER_NOT_FOUND, None

        is_owner = bool(actor_telegram_id is not None and user.telegram_id == actor_telegram_id)
        is_admin_actor = bool(actor_telegram_id is not None and is_admin(actor_telegram_id))
        if not (is_owner or is_admin_actor):
            return False, texts.WL_ADMIN_ONLY_ALERT, None

        sub = await white_internet_repo.get_subscription_by_user_id(session, user_id)
        if sub is None:
            return False, texts.WL_NO_SUB, None
        if sub.user_id != user.id:
            return False, texts.ERROR_ACCESS_DENIED, None
        if getattr(sub, "is_trial", False):
            return False, texts.WL_TRIAL_CANNOT_ADD_DEVICE, None
        now = now_utc()
        if sub.status in (WhiteInternetStatus.PENDING, WhiteInternetStatus.DISABLED):
            return False, texts.WL_SUB_NOT_READY, None
        if sub.status == WhiteInternetStatus.EXPIRED or (sub.expires_at and sub.expires_at <= now):
            return False, texts.WL_SUB_EXPIRED, None

        current_limit = max(1, getattr(sub, "device_limit", 1) or 1)
        if current_limit >= WHITE_INTERNET_MAX_DEVICE_LIMIT:
            return False, texts.WL_DEVICE_LIMIT_MAX_REACHED, None

        total_accumulated = (
            (sub.base_traffic_bytes or 0)
            + (sub.extra_traffic_bytes or 0)
            + WHITE_INTERNET_EXTRA_DEVICE_TRAFFIC_BYTES
        )
        if total_accumulated > WHITE_INTERNET_MAX_QUOTA_BYTES:
            current_available = await white_internet_repo.get_available_quota_bytes(
                session, sub.id, now
            )
            return (
                False,
                texts.WL_CAP_EXCEEDED.format(
                    gb=int(WHITE_INTERNET_EXTRA_DEVICE_TRAFFIC_BYTES / (1024**3)),
                    available=current_available // (1024**3),
                ),
                None,
            )

        # Pre-Debit Validation: validate origin node health & availability before debiting funds
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
                logger.warning("No healthy origin node available for device slot migration: %s", exc)
                return False, texts.WL_NO_SERVERS_AVAILABLE, None

        price = WHITE_INTERNET_EXTRA_DEVICE_PRICE_RUB
        tariff = await cls.get_or_create_white_internet_tariff(session)
        tariff_version = await get_or_create_current_version(session, tariff)
        quote = cls._new_quote(
            user_id=user.id,
            operation_type=TariffQuoteOperation.PURCHASE,
            target_version_id=tariff_version.id,
            amount_due=price,
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
                texts.WL_INSUFFICIENT_BALANCE_BUY.format(
                    price=int(price),
                    balance=balance_snap.available,
                    shortage=max(price - balance_snap.available, Decimal(0)),
                ),
                None,
            )
        except AccountLedgerError as exc:
            quote.status = TariffQuoteStatus.CANCELLED
            await session.flush()
            return False, f"{texts.WL_DEBIT_FAILED}: {exc}", None

        # Apply node migration ONLY after successful financial debit
        old_origin_for_cleanup: Server | None = None
        if needs_migration and new_origin_server is not None:
            old_origin_for_cleanup = origin_node
            sub.origin_node_id = new_origin_server.id
            sub.actual_version = 0
            sub.last_reconciled_node_epoch = None
            sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_CREATE
            await session.flush()
            await white_internet_repo.cancel_pending_orphan_cleanups_for_client(
                session, new_origin_server.id, sub.uuid
            )

        updated_sub = await white_internet_repo.add_device_slot_atomic(
            session,
            subscription_id=sub.id,
            extra_bytes=WHITE_INTERNET_EXTRA_DEVICE_TRAFFIC_BYTES,
        )
        quote.status = TariffQuoteStatus.CONSUMED
        quote.consumed_at = now_utc()

        if old_origin_for_cleanup:
            await white_internet_repo.enqueue_orphan_cleanup(
                session,
                server_id=old_origin_for_cleanup.id,
                client_uuid=sub.uuid,
                desired_version=sub.desired_version + 1,
            )

        await session.flush()

        if old_origin_for_cleanup:
            _dispatch_deprovision(
                old_origin_for_cleanup,
                client_uuid=sub.uuid,
                version=sub.desired_version + 1,
                context=f"add_device_slot sub {sub.id}",
            )

        return True, texts.WL_ADD_DEVICE_SUCCESS.format(limit=updated_sub.device_limit), updated_sub

    @classmethod
    async def topup_quota(
        cls,
        session: AsyncSession,
        user_id: int,
        pack_gb: int,
        actor_telegram_id: int | None = None,
    ):
        if pack_gb not in WHITE_INTERNET_TOPUP_PACKS:
            return False, texts.WL_INVALID_TOPUP_PACK.format(gb=pack_gb), None
        pack_price = WHITE_INTERNET_TOPUP_PACKS[pack_gb]
        user = await lock_checkout_user(session, user_id)
        if user is None:
            return False, texts.WL_USER_NOT_FOUND, None

        is_owner = bool(actor_telegram_id is not None and user.telegram_id == actor_telegram_id)
        is_admin_actor = bool(actor_telegram_id is not None and is_admin(actor_telegram_id))
        if not (is_owner or is_admin_actor):
            return False, texts.WL_ADMIN_ONLY_ALERT, None

        sub = await white_internet_repo.get_subscription_by_user_id(session, user_id)
        if sub is None:
            return False, texts.WL_NO_SUB, None
        if sub.user_id != user.id:
            return False, texts.ERROR_ACCESS_DENIED, None
        if getattr(sub, "is_trial", False):
            return False, texts.WL_TRIAL_CANNOT_TOPUP, None
        now = now_utc()
        if sub.status in (WhiteInternetStatus.PENDING, WhiteInternetStatus.DISABLED):
            return False, texts.WL_SUB_NOT_READY, None
        if sub.status == WhiteInternetStatus.EXPIRED or (sub.expires_at and sub.expires_at <= now):
            return False, texts.WL_SUB_EXPIRED, None

        # Pre-Debit Validation: validate origin node health & availability before debiting funds
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
                logger.warning("No healthy origin node available for topup migration: %s", exc)
                return False, texts.WL_NO_SERVERS_AVAILABLE, None

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

        # Apply node migration ONLY after successful financial debit
        old_origin_for_cleanup: Server | None = None
        if needs_migration and new_origin_server is not None:
            old_origin_for_cleanup = origin_node
            sub.origin_node_id = new_origin_server.id
            sub.actual_version = 0
            sub.last_reconciled_node_epoch = None
            sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_CREATE
            await session.flush()
            await white_internet_repo.cancel_pending_orphan_cleanups_for_client(
                session, new_origin_server.id, sub.uuid
            )

        grant = await white_internet_repo.topup_quota_atomic(
            session,
            subscription_id=sub.id,
            quote_id=quote.id,
            pack_gb=pack_gb,
            price_rub=pack_price,
        )
        quote.status = TariffQuoteStatus.CONSUMED
        quote.consumed_at = now_utc()

        if old_origin_for_cleanup:
            await white_internet_repo.enqueue_orphan_cleanup(
                session,
                server_id=old_origin_for_cleanup.id,
                client_uuid=sub.uuid,
                desired_version=sub.desired_version + 1,
            )

        await session.flush()

        if old_origin_for_cleanup:
            _dispatch_deprovision(
                old_origin_for_cleanup,
                client_uuid=sub.uuid,
                version=sub.desired_version + 1,
                context=f"topup sub {sub.id}",
            )

        return True, texts.WL_TOPUP_SUCCESS.format(gb=pack_gb), grant

    @classmethod
    async def create_trial_subscription(cls, session: AsyncSession, user_id: int):
        """Provision a free trial White Internet subscription (3 days / 5 GiB / 0 RUB)."""
        user = await lock_checkout_user(session, user_id)
        if user is None:
            return False, texts.WL_USER_NOT_FOUND, None
        if user.is_banned or user.is_deleted:
            return False, texts.ERROR_ACCESS_DENIED, None

        has_trial = await white_internet_repo.has_ever_activated_trial(session, user_id)
        if has_trial:
            existing = await white_internet_repo.get_subscription_by_user_id(session, user_id)
            return False, texts.WL_TRIAL_ALREADY_USED, existing

        existing = await white_internet_repo.get_subscription_by_user_id(session, user_id)
        if existing:
            if existing.status in (
                WhiteInternetStatus.ACTIVE,
                WhiteInternetStatus.PENDING,
                WhiteInternetStatus.EXHAUSTED,
            ):
                return True, texts.WL_ALREADY_ACTIVE, existing
            if (
                getattr(existing, "pending_hard_delete", False)
                or getattr(existing, "provisioning_status", None)
                == WhiteInternetProvisioningStatus.PENDING_DELETE
            ):
                return False, texts.WL_DEACTIVATION_PENDING, existing
            return False, texts.WL_TRIAL_ALREADY_USED, existing

        try:
            origin_node = await cls.select_origin_node(session)
        except RuntimeError as exc:
            logger.warning("No available origin node for white internet trial: %s", exc)
            return False, texts.WL_NO_SERVERS_AVAILABLE, None

        now = now_utc()
        tariff = await cls.get_or_create_white_internet_tariff(session)
        tariff_version = await get_or_create_current_version(session, tariff)

        quote = cls._new_quote(
            user_id=user.id,
            operation_type=TariffQuoteOperation.TRIAL,
            target_version_id=tariff_version.id,
            amount_due=Decimal("0.00"),
            expires_at=now + timedelta(minutes=15),
            resulting_paid_hours=WHITE_INTERNET_TRIAL_DURATION_DAYS * 24,
            resulting_paid_value=Decimal("0.00"),
        )
        quote.status = TariffQuoteStatus.CONSUMED
        quote.consumed_at = now
        session.add(quote)
        await session.flush()

        sub_token = secrets.token_hex(32)
        sub_uuid = str(uuid.uuid4())

        sub = await white_internet_repo.create_white_internet_subscription(
            session,
            user_id=user.id,
            origin_node_id=origin_node.id,
            token=sub_token,
            uuid=sub_uuid,
            quote_id=quote.id,
            price_rub=Decimal("0.00"),
            duration_days=WHITE_INTERNET_TRIAL_DURATION_DAYS,
            base_bytes=WHITE_INTERNET_TRIAL_TRAFFIC_BYTES,
            is_trial=True,
        )

        # Commit DB state before executing external network sync.
        # This durably persists quote and trial subscription in PostgreSQL
        # and releases all SELECT FOR UPDATE row locks (Server, User) so concurrent
        # operations are not blocked during external network I/O.
        # If commit fails, we fail-closed immediately WITHOUT mutating Xray.
        await session.commit()

        # Zero-Wait UX: Synchronous provisioning on Xray node.
        await cls._try_inline_sync(
            session, sub, origin_node, idempotency_key=f"trial:{sub.id}:1:True"
        )

        return True, texts.WL_TRIAL_ACTIVATED_SUCCESS, sub

    @classmethod
    async def _try_inline_sync(
        cls,
        session: AsyncSession,
        sub: WhiteInternetSubscription,
        origin_node: Server,
        idempotency_key: str,
    ) -> bool:
        """Attempt best-effort synchronous activation on origin node (timeout=4.0s).

        Fail-closed like the reconciliation worker: mark ACTIVE only when the
        node confirms the expected epoch and full inbound coverage; verify
        ALREADY_NEWER against runtime inventory. Anything unconfirmed stays
        PENDING_CREATE for the background worker to converge.
        """
        expected_inbound_tags: set[str] = set()
        for relay in (origin_node.extra_data or {}).get("relays", []) or []:
            code = (relay or {}).get("code")
            if code:
                expected_inbound_tags.add(f"just1k-wl-inbound-{code}")
        if not expected_inbound_tags:
            expected_inbound_tags.add("just1k-wl-default")
        try:
            async with XrayNodeClient(timeout=4.0) as xray_client:
                resp = await xray_client.sync_client(
                    origin_node.api_url,
                    origin_node.api_key,
                    client_uuid=sub.uuid,
                    is_active=True,
                    version=sub.desired_version or 1,
                    expected_node_epoch=origin_node.xray_instance_epoch,
                    idempotency_key=idempotency_key,
                )
                sync_res = resp.result if hasattr(resp, "result") else resp[0]
                verified_epoch = (
                    getattr(resp, "verified_epoch", None) or origin_node.xray_instance_epoch
                )
                verified_inbounds = set(getattr(resp, "verified_inbounds", None) or [])
                epoch_ok = verified_epoch == origin_node.xray_instance_epoch
                inbounds_ok = (
                    not verified_inbounds
                    or expected_inbound_tags.issubset(verified_inbounds)
                )
                confirmed = False
                if sync_res == SyncResult.APPLIED and epoch_ok and inbounds_ok:
                    confirmed = True
                elif sync_res == SyncResult.ALREADY_NEWER and epoch_ok:
                    inv_ok, inv_data, _ = await xray_client.get_inventory(
                        origin_node.api_url,
                        origin_node.api_key,
                        client_ids=[sub.uuid],
                    )
                    observed = None
                    if inv_ok and inv_data and "inventory" in inv_data:
                        observed = (inv_data["inventory"].get(sub.uuid) or {}).get(
                            "observed_state"
                        )
                    confirmed = inv_ok and observed == "active" and inbounds_ok
                if confirmed:
                    sub.status = WhiteInternetStatus.ACTIVE
                    sub.actual_version = sub.desired_version or 1
                    sub.provisioning_status = WhiteInternetProvisioningStatus.ACTIVE
                    sub.last_reconciled_node_epoch = verified_epoch
                    sub.last_synced_at = now_utc()
                    await session.flush()
                    try:
                        await session.commit()
                    except Exception as exc:
                        await session.rollback()
                        logger.warning(
                            "Session commit after inline sync failed, leaving PENDING_CREATE: %s", exc
                        )
                        return False
                    return True
                else:
                    logger.warning(
                        "Inline sync unconfirmed (result=%s epoch_ok=%s inbounds_ok=%s), "
                        "leaving subscription %d PENDING_CREATE for worker",
                        sync_res,
                        epoch_ok,
                        inbounds_ok,
                        sub.id,
                    )
        except Exception as exc:
            try:
                await session.rollback()
            except Exception:
                pass
            logger.warning(
                "Synchronous activation fallback to background worker: %s", exc
            )
        return False

    @classmethod
    async def deactivate_user_subscriptions(
        cls,
        session: AsyncSession,
        user_id: int,
        reason: str = "user_banned",
    ) -> list[WhiteInternetSubscription]:
        """Deactivate and deprovision all White Internet subscriptions for a user (e.g. on ban)."""
        stmt = (
            select(WhiteInternetSubscription)
            .where(
                WhiteInternetSubscription.user_id == user_id,
                WhiteInternetSubscription.status != WhiteInternetStatus.DISABLED,
            )
            .with_for_update()
        )
        res = await session.execute(stmt)
        subs = list(res.scalars().all())

        deactivated: list[WhiteInternetSubscription] = []
        for sub in subs:
            sub.status = WhiteInternetStatus.DISABLED
            sub.status_reason = reason
            sub.desired_version += 1
            sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_DELETE
            deactivated.append(sub)

        await session.flush()

        for sub in deactivated:
            if sub.origin_node_id:
                origin_server = await session.get(Server, sub.origin_node_id)
                _dispatch_deprovision(
                    origin_server,
                    client_uuid=sub.uuid,
                    version=sub.desired_version,
                    context=f"deactivate sub {sub.id}",
                )

        return deactivated

    @classmethod
    async def reset_user_trial(
        cls,
        session: AsyncSession,
        user_id: int,
    ) -> tuple[bool, str]:
        """Reset trial history for a user, allowing a new trial to be issued."""
        user = await lock_checkout_user(session, user_id)
        if user is None:
            return False, texts.WL_USER_NOT_FOUND

        now = now_utc()
        last_reset = getattr(user, "last_trial_reset_at", None)
        if isinstance(last_reset, datetime):
            if last_reset.tzinfo is None:
                last_reset = last_reset.replace(tzinfo=timezone.utc)
            elapsed = (now - last_reset).total_seconds()
            if elapsed < 60:
                remaining = int(60 - elapsed)
                return False, texts.ADMIN_WL_RESET_COOLDOWN.format(seconds=remaining)

        stmt = (
            select(WhiteInternetSubscription)
            .where(WhiteInternetSubscription.user_id == user_id)
            .with_for_update()
        )
        res = await session.execute(stmt)
        subs = list(res.scalars().all())

        if not subs:
            return False, texts.WL_SUB_NOT_FOUND

        user.last_trial_reset_at = now

        trial_subs = [s for s in subs if getattr(s, "is_trial", False)]
        if not trial_subs:
            await session.flush()
            return True, texts.ADMIN_WL_RESET_SUCCESS

        # Two-phase reset (durable-by-default): mark DISABLED+PENDING_DELETE only for trial subs.
        # Paid subscriptions (is_trial=False) are left completely untouched.
        for sub in trial_subs:
            sub.status = WhiteInternetStatus.DISABLED
            sub.status_reason = "trial_reset"
            sub.desired_version += 1
            sub.provisioning_status = WhiteInternetProvisioningStatus.PENDING_DELETE
            sub.pending_hard_delete = True

        await session.flush()

        for sub in trial_subs:
            if sub.origin_node_id:
                origin_server = await session.get(Server, sub.origin_node_id)
                _dispatch_deprovision(
                    origin_server,
                    client_uuid=sub.uuid,
                    version=sub.desired_version,
                    context=f"reset sub {sub.id}",
                )

        return True, texts.ADMIN_WL_RESET_SUCCESS

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
            "xPaddingBytes": CANONICAL_XHTTP_PROFILE.get("xPaddingBytes", "100-1000"),
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
        """Generate complete Xray client JSON config for INCY."""
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
                            "xPaddingBytes": CANONICAL_XHTTP_PROFILE.get("xPaddingBytes", "100-1000"),
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
