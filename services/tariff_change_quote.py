"""Creation-only tariff change quotes; no payment or entitlement side effects."""
from __future__ import annotations

import hashlib
import logging
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING

from sqlalchemy import select

from database.models import Tariff, TariffQuote, TariffVersion
from database.repositories.account_ledger_repo import get_account_balance
from database.repositories.profiles_repo import get_user_profiles_count
from database.repositories.tariff_quotes_repo import (
    QUOTE_LIFETIME, get_active_financial_quotes_for_update,
    get_or_create_current_version, lock_checkout_user,
)
from services.subscription_balance_service import get_subscription_balance_snapshot
from services.tariff_value_calculator import (
    TariffCalculationError, TariffVersionSnapshot, calculate_tariff_value,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TariffChangeQuoteResult:
    quote: TariffQuote | None = None
    created: bool = False
    failure_code: str | None = None
    snapshot_failure_code: str | None = None


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


def balance_snapshot_fingerprint(*, user_id: int, subscription_end: datetime, snapshot) -> str:
    """SHA-256 of stable JSON: UTC timestamps, fixed decimals and sorted lots/IDs."""
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
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


async def create_tariff_change_quote(session, *, user_id: int, target_tariff_id: int,
                                     as_of: datetime) -> TariffChangeQuoteResult:
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
        snapshot_temp = await get_subscription_balance_snapshot(
            session, user_id=user_id, as_of=as_of, locked_user=user
        )
        if snapshot_temp.paid_lots:
            v_ids = {lot.tariff_version_id for lot in snapshot_temp.paid_lots}
            t_ids = set(await session.scalars(select(TariffVersion.tariff_id).where(TariffVersion.id.in_(v_ids))))
            if len(t_ids) == 1:
                current_tariff_id = next(iter(t_ids))
        if current_tariff_id is None:
            matched_tariff_id = await session.scalar(
                select(Tariff.id).where(
                    Tariff.device_limit == user.device_limit,
                    Tariff.is_active.is_(True),
                ).limit(1)
            )
            if matched_tariff_id is not None:
                current_tariff_id = matched_tariff_id
        if current_tariff_id is not None:
            user.current_tariff_id = current_tariff_id
            await session.flush()

    if current_tariff_id is None:
        return TariffChangeQuoteResult(failure_code="current_tariff_unknown")

    active = await get_active_financial_quotes_for_update(
        session, user_id=user_id, as_of=as_of)
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
    if target.id == user.current_tariff_id:
        return TariffChangeQuoteResult(failure_code="same_tariff_requires_renew")
    if source.duration_days <= 0 or source.price_rub <= 0 or source.device_limit <= 0 \
            or target.duration_days <= 0 or target.price_rub <= 0 or target.device_limit <= 0:
        return TariffChangeQuoteResult(failure_code="target_tariff_inactive")
    profiles = await get_user_profiles_count(session, user_id)
    if profiles > target.device_limit:
        return TariffChangeQuoteResult(failure_code="target_device_limit_too_small")

    existing_change = next((q for q in active if q.operation_type == "change"), None)
    snapshot = await get_subscription_balance_snapshot(
        session, user_id=user_id, as_of=as_of, locked_user=user)
    if not snapshot.tracked or snapshot.remaining_paid_value_rub is None:
        logger.warning(
            "create_tariff_change_quote untracked balance: user_id=%s, snapshot_failure_code=%s, coverage_end=%s, subscription_end=%s",
            user_id,
            snapshot.failure_code,
            snapshot.coverage_end,
            user.subscription_end,
        )
        if existing_change is not None:
            existing_change.status = "cancelled"
            existing_change.diagnostic_reason = "source_balance_untracked"
            await session.flush()
        return TariffChangeQuoteResult(
            failure_code="subscription_balance_untracked",
            snapshot_failure_code=snapshot.failure_code,
        )

    version_ids = {lot.tariff_version_id for lot in snapshot.paid_lots}
    lot_versions = (await session.scalars(select(TariffVersion).where(TariffVersion.id.in_(version_ids)))).all() if version_ids else []
    if len(lot_versions) != len(version_ids):
        logger.warning(
            "create_tariff_change_quote missing_tariff_versions: user_id=%s, version_ids=%s",
            user_id,
            version_ids,
        )
        return TariffChangeQuoteResult(failure_code="mixed_source_tariffs")
    source_version = await get_or_create_current_version(session, source)
    target_version = await get_or_create_current_version(session, target)

    if existing_change:
        source_version_tariff_id = await session.scalar(select(TariffVersion.tariff_id).where(
            TariffVersion.id == existing_change.source_tariff_version_id))
        same_history = (
            _timestamp(existing_change.source_subscription_end) == _timestamp(user.subscription_end)
            and sorted(existing_change.source_entitlement_entry_ids or [])
                == sorted(snapshot.source_entitlement_entry_ids)
            and sorted(existing_change.source_ledger_entry_ids or [])
                == sorted(snapshot.source_ledger_entry_ids)
            and source_version_tariff_id == user.current_tariff_id
        )
        if existing_change.target_tariff_version_id == target_version.id and same_history:
            return TariffChangeQuoteResult(existing_change, False, None)

        existing_change.status = "cancelled"
        existing_change.diagnostic_reason = (
            "source_balance_changed" if not same_history else "superseded_by_new_target"
        )
        await session.flush()
        existing_change = None

    required = max(Decimal(0), target_version.price_rub - snapshot.remaining_paid_value_rub).quantize(
        Decimal("1"), rounding=ROUND_CEILING)
    try:
        calculation = calculate_tariff_value(
            operation_type="change", source_paid_hours=snapshot.remaining_paid_hours,
            source_paid_value_rub=snapshot.remaining_paid_value_rub,
            source_tariff=TariffVersionSnapshot(source.id, source_version.id, source_version.duration_hours,
                                                source_version.price_rub, source_version.currency),
            target_tariff=TariffVersionSnapshot(target.id, target_version.id, target_version.duration_hours,
                                                target_version.price_rub, target_version.currency),
            confirmed_additional_payment_rub=required, bonus_hours=snapshot.remaining_bonus_hours)
    except TariffCalculationError as exc:
        logger.warning(
            "create_tariff_change_quote TariffCalculationError for user_id=%s: %s",
            user_id,
            exc,
        )
        return TariffChangeQuoteResult(
            failure_code="subscription_balance_untracked",
            snapshot_failure_code="tariff_calculation_error",
        )
    fingerprint = balance_snapshot_fingerprint(user_id=user_id, subscription_end=user.subscription_end,
                                               snapshot=snapshot)
    quote = TariffQuote(
        public_id=uuid.uuid4(), user_id=user_id, operation_type="change",
        source_tariff_version_id=source_version.id, target_tariff_version_id=target_version.id,
        current_paid_hours=snapshot.remaining_paid_hours,
        current_paid_value_rub=snapshot.remaining_paid_value_rub,
        bonus_hours=snapshot.remaining_bonus_hours, amount_due_rub=required,
        resulting_paid_hours=calculation.resulting_paid_hours,
        resulting_paid_value_rub=calculation.paid_value_after_rub,
        resulting_bonus_hours=calculation.retained_bonus_hours,
        rounding_loss_hours=calculation.rounding_loss_hours,
        rounding_loss_value_rub=calculation.rounding_loss_value_rub,
        currency="RUB", status="active", created_at=as_of, expires_at=as_of + QUOTE_LIFETIME,
        balance_as_of=as_of, source_subscription_end=user.subscription_end,
        source_balance_fingerprint=fingerprint,
        source_entitlement_entry_ids=sorted(snapshot.source_entitlement_entry_ids),
        source_ledger_entry_ids=sorted(snapshot.source_ledger_entry_ids) )
    session.add(quote)
    await session.flush()
    return TariffChangeQuoteResult(quote, True, None)
