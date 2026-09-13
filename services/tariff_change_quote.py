"""Creation-only tariff change quotes; no payment or entitlement side effects."""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import ROUND_CEILING, Decimal

from sqlalchemy import select

from config.enums import ServiceType
from database.models import EntitlementEntry, Tariff, TariffQuote, User
from database.repositories.account_ledger_repo import get_account_balance
from database.repositories.profiles_repo import (
    get_user_effective_device_count,
)
from database.repositories.tariff_quotes_repo import (
    QUOTE_LIFETIME,
    get_active_financial_quotes_for_update,
    get_or_create_current_version,
    lock_checkout_user,
)
from database.repositories.tariffs_repo import get_tariff_by_id

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TariffChangeQuoteResult:
    quote: TariffQuote | None = None
    created: bool = False
    failure_code: str | None = None
    snapshot_failure_code: str | None = None


@dataclass(frozen=True)
class TariffChangeOptions:
    remaining_days: int
    remaining_value: int
    source_tariff_name: str
    target_tariff_name: str
    target_device_limit: int
    option1_available: bool
    option1_days: int
    option1_leftover_rub: int
    option2_1m_surcharge: int
    option2_1m_tariff_id: int
    option2_3m_surcharge: int | None = None
    option2_3m_tariff_id: int | None = None


class SnapshotCanonicalizationError(ValueError):
    """A snapshot contains a value without a safe canonical representation."""


def _decimal(value: Decimal) -> str:
    value = Decimal(value)
    if not value.is_finite():
        raise SnapshotCanonicalizationError("Decimal must be finite")
    if value == 0:
        return "0"
    fixed = format(value, "f")
    if "." in fixed:
        fixed = fixed.rstrip("0").rstrip(".")
    return fixed


def _timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        raise SnapshotCanonicalizationError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def balance_snapshot_fingerprint(*, user_id: int, subscription_end: datetime, snapshot=None, **kwargs) -> str:
    """SHA-256 of stable JSON: UTC timestamps, fixed decimals and sorted lots/IDs."""
    if snapshot is not None and hasattr(snapshot, "paid_lots") and snapshot.paid_lots:
        paid = [{
            "entitlement_id": x.entitlement_entry_id, "ledger_id": x.paid_value_ledger_entry_id,
            "tariff_version_id": x.tariff_version_id,
            "quote_id": x.quote_id,
            "remaining_hours": x.remaining_whole_hours,
            "remaining_value": _decimal(x.remaining_paid_value_rub),
            "segment_start": _timestamp(x.segment_start), "segment_end": _timestamp(x.segment_end),
        } for x in snapshot.paid_lots]
        bonus = [{
            "entitlement_id": x.entitlement_entry_id, "source_type": x.source_type,
            "source_id": x.source_id, "type": x.bonus_type,
            "remaining_hours": x.remaining_whole_hours,
            "segment_start": _timestamp(x.segment_start), "segment_end": _timestamp(x.segment_end),
        } for x in snapshot.bonus_lots]
        body = {
            "user_id": user_id, "balance_as_of": _timestamp(snapshot.as_of),
            "subscription_end": _timestamp(subscription_end),
            "remaining_paid_hours": snapshot.remaining_paid_hours,
            "remaining_paid_value": _decimal(snapshot.remaining_paid_value_rub),
            "remaining_bonus_hours": snapshot.remaining_bonus_hours,
            "rounding_loss_hours": _decimal(snapshot.rounding_loss_hours),
            "paid_lots": sorted(paid, key=lambda x: (x["entitlement_id"], x["ledger_id"])),
            "bonus_lots": sorted(bonus, key=lambda x: (x["entitlement_id"], x["source_type"], x["source_id"])),
            "source_entitlement_ids": sorted(snapshot.source_entitlement_entry_ids),
            "source_ledger_ids": sorted(snapshot.source_ledger_entry_ids),
        }
    else:
        body = {
            "user_id": user_id,
            "subscription_end": _timestamp(subscription_end),
            **{k: str(v) for k, v in sorted(kwargs.items())},
        }
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


async def calculate_tariff_change_options(
    session,
    *,
    user: User,
    target_tariff: Tariff,
    as_of: datetime,
) -> TariffChangeOptions:
    """Calculate Option 1 (transfer >=7d) and Option 2 (1m/3m surcharge) for presentation."""
    source_tariff = None
    if user.current_tariff_id:
        source_tariff = await get_tariff_by_id(session, user.current_tariff_id)
    if source_tariff is None:
        source_tariff = await session.scalar(
            select(Tariff).where(
                Tariff.device_limit == user.device_limit,
                Tariff.is_active.is_(True),
            ).order_by(Tariff.duration_days.asc()).limit(1)
        )
    if source_tariff is None:
        source_daily_rate = Decimal(3)
        source_tariff_name = "Базовый"
    else:
        source_daily_rate = Decimal(source_tariff.price_rub) / Decimal(source_tariff.duration_days)
        source_tariff_name = getattr(source_tariff, "name", "Текущий")

    remaining_seconds = max(0, (user.subscription_end - as_of).total_seconds()) if user.subscription_end else 0
    remaining_days = max(1, int(round(remaining_seconds / 86400))) if remaining_seconds > 0 else 0
    remaining_value = int(round(Decimal(remaining_days) * source_daily_rate))

    target_tariffs = (await session.scalars(
        select(Tariff).where(
            Tariff.device_limit == target_tariff.device_limit,
            Tariff.is_active.is_(True),
        ).order_by(Tariff.duration_days.asc())
    )).all()

    target_30d = next((t for t in target_tariffs if t.duration_days == 30), target_tariff)
    target_90d = next((t for t in target_tariffs if t.duration_days == 90), None)

    target_daily_rate = Decimal(target_30d.price_rub) / Decimal(target_30d.duration_days)
    opt1_days = int(Decimal(remaining_value) // target_daily_rate)
    opt1_leftover = max(0, remaining_value - int(round(Decimal(opt1_days) * target_daily_rate)))
    opt1_available = opt1_days >= 7

    due_1m = max(0, int(target_30d.price_rub) - remaining_value)
    due_3m = max(0, int(target_90d.price_rub) - remaining_value) if target_90d else None

    return TariffChangeOptions(
        remaining_days=remaining_days,
        remaining_value=remaining_value,
        source_tariff_name=source_tariff_name,
        target_tariff_name=target_30d.name,
        target_device_limit=target_tariff.device_limit,
        option1_available=opt1_available,
        option1_days=opt1_days,
        option1_leftover_rub=opt1_leftover,
        option2_1m_surcharge=due_1m,
        option2_1m_tariff_id=target_30d.id,
        option2_3m_surcharge=due_3m,
        option2_3m_tariff_id=target_90d.id if target_90d else None,
    )


async def create_tariff_change_quote(
    session,
    *,
    user_id: int,
    target_tariff_id: int,
    as_of: datetime,
    option_type: str = "surcharge",
) -> TariffChangeQuoteResult:
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    user = await lock_checkout_user(session, user_id)
    if user is None:
        return TariffChangeQuoteResult(failure_code="user_not_found")
    if user.is_deleted or user.is_banned or user.is_bot_blocked:
        return TariffChangeQuoteResult(failure_code="user_ineligible")
    if user.financial_hold:
        return TariffChangeQuoteResult(failure_code="financial_hold")
    account = await get_account_balance(
        session, user_id=user.id, locked_user=user
    )
    if account.debt > 0:
        return TariffChangeQuoteResult(failure_code="account_debt")
    if user.subscription_end is None or user.subscription_end <= as_of:
        return TariffChangeQuoteResult(failure_code="subscription_inactive")

    current_tariff_id = user.current_tariff_id
    if current_tariff_id is None:
        matched_tariff_id = await session.scalar(
            select(Tariff.id).where(
                Tariff.device_limit == user.device_limit,
                Tariff.is_active.is_(True),
            ).order_by(
                Tariff.duration_days.asc(),
                Tariff.sort_order.asc(),
                Tariff.id.asc(),
            ).limit(1)
        )
        if matched_tariff_id is not None:
            current_tariff_id = matched_tariff_id
            user.current_tariff_id = current_tariff_id
            await session.flush()

    if current_tariff_id is None:
        return TariffChangeQuoteResult(failure_code="current_tariff_unknown")

    active = await get_active_financial_quotes_for_update(
        session, user_id=user_id, as_of=as_of
    )
    if any(q.operation_type in {"purchase", "renew"} for q in active):
        return TariffChangeQuoteResult(failure_code="active_checkout_exists")

    tariffs = (await session.scalars(select(Tariff).where(
        Tariff.id.in_({current_tariff_id, target_tariff_id})
    ).order_by(Tariff.id).with_for_update())).all()
    by_id = {x.id: x for x in tariffs}
    source = by_id.get(current_tariff_id)
    target = by_id.get(target_tariff_id)
    if source is None:
        return TariffChangeQuoteResult(failure_code="current_tariff_unknown")
    if target is None:
        return TariffChangeQuoteResult(failure_code="target_tariff_not_found")
    if not target.is_active:
        return TariffChangeQuoteResult(failure_code="target_tariff_inactive")
    source_service = getattr(source, "service_type", None) or ServiceType.AWG
    target_service = getattr(target, "service_type", None) or ServiceType.AWG
    if source_service != target_service or target_service != ServiceType.AWG:
        return TariffChangeQuoteResult(failure_code="cross_protocol_forbidden")
    if target.id == user.current_tariff_id or target.device_limit == source.device_limit:
        return TariffChangeQuoteResult(failure_code="same_tariff_requires_renew")
    if source.duration_days <= 0 or source.price_rub <= 0 or source.device_limit <= 0 \
            or target.duration_days <= 0 or target.price_rub <= 0 or target.device_limit <= 0:
        return TariffChangeQuoteResult(failure_code="target_tariff_inactive")

    # Additive device check
    effective_devices = await get_user_effective_device_count(
        session, user_id, user.active_sub_devices
    )
    if effective_devices > target.device_limit:
        return TariffChangeQuoteResult(failure_code="target_device_limit_too_small")

    # Cooldown check for downgrade
    is_downgrade = target.device_limit < source.device_limit
    if is_downgrade:
        last_change_entry = await session.scalar(
            select(EntitlementEntry)
            .where(
                EntitlementEntry.beneficiary_user_id == user.id,
                EntitlementEntry.entry_type == "tariff_change",
            )
            .order_by(EntitlementEntry.created_at.desc())
            .limit(1)
        )
        if last_change_entry is not None:
            last_change_at = last_change_entry.created_at
            if last_change_at.tzinfo is None:
                last_change_at = last_change_at.replace(tzinfo=timezone.utc)
            meta = last_change_entry.metadata_ or {}
            was_last_downgrade = bool(meta.get("is_downgrade", False))
            if was_last_downgrade and (as_of - last_change_at) < timedelta(hours=24):
                return TariffChangeQuoteResult(failure_code="change_cooldown_active")

    source_version = await get_or_create_current_version(session, source)
    target_version = await get_or_create_current_version(session, target)

    # Daily calculation in whole rubles
    remaining_seconds = max(0, (user.subscription_end - as_of).total_seconds())
    remaining_days = max(1, int(round(remaining_seconds / 86400)))
    source_daily_rate = Decimal(source_version.price_rub) / Decimal(source_version.duration_days)
    remaining_value = Decimal(int(round(Decimal(remaining_days) * source_daily_rate)))

    current_paid_hours = remaining_days * 24
    current_paid_value_rub = remaining_value

    if option_type == "transfer":
        target_daily_rate = Decimal(target_version.price_rub) / Decimal(target_version.duration_days)
        new_days = int(remaining_value // target_daily_rate)
        if new_days < 7:
            return TariffChangeQuoteResult(failure_code="transfer_below_minimum_days")
        leftover_rub = max(Decimal(0), remaining_value - int(round(Decimal(new_days) * target_daily_rate)))
        required = Decimal(0)
        resulting_paid_hours = new_days * 24
        rounding_loss_value_rub = leftover_rub
        resulting_paid_value_rub = remaining_value - leftover_rub
    else:
        # Surcharge option
        required = max(Decimal(0), target_version.price_rub - remaining_value).quantize(
            Decimal(1), rounding=ROUND_CEILING
        )
        resulting_paid_hours = target_version.duration_hours
        rounding_loss_value_rub = Decimal(0)
        resulting_paid_value_rub = target_version.price_rub

    existing_change = next((q for q in active if q.operation_type == "change"), None)
    if existing_change:
        same_target = (existing_change.target_tariff_version_id == target_version.id)
        same_sub_end = (
            existing_change.source_subscription_end is not None
            and _timestamp(existing_change.source_subscription_end) == _timestamp(user.subscription_end)
        )
        same_amount = (existing_change.amount_due_rub == required)
        if same_target and same_sub_end and same_amount:
            return TariffChangeQuoteResult(
                quote=existing_change,
                created=False,
                failure_code=None,
                snapshot_failure_code=None,
            )

        existing_change.status = "cancelled"
        existing_change.diagnostic_reason = (
            "source_balance_changed" if not same_sub_end else "superseded_by_new_target"
        )
        await session.flush()
        existing_change = None

    fingerprint = balance_snapshot_fingerprint(
        user_id=user_id,
        subscription_end=user.subscription_end,
        source_version_id=source_version.id,
        target_version_id=target_version.id,
        amount_due=required,
        option_type=option_type,
    )
    quote = TariffQuote(
        public_id=uuid.uuid4(),
        user_id=user_id,
        operation_type="change",
        service_type=getattr(target, "service_type", None) or "awg",
        source_tariff_version_id=source_version.id,
        target_tariff_version_id=target_version.id,
        current_paid_hours=current_paid_hours,
        current_paid_value_rub=current_paid_value_rub,
        bonus_hours=0,
        amount_due_rub=required,
        resulting_paid_hours=resulting_paid_hours,
        resulting_paid_value_rub=resulting_paid_value_rub,
        resulting_bonus_hours=0,
        rounding_loss_hours=Decimal(0),
        rounding_loss_value_rub=rounding_loss_value_rub,
        currency="RUB",
        status="active",
        created_at=as_of,
        expires_at=as_of + QUOTE_LIFETIME,
        balance_as_of=as_of,
        source_subscription_end=user.subscription_end,
        source_balance_fingerprint=fingerprint,
        source_entitlement_entry_ids=[],
        source_ledger_entry_ids=[],
    )
    session.add(quote)
    await session.flush()
    return TariffChangeQuoteResult(quote, True, None)
