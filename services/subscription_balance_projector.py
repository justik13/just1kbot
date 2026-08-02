"""Pure, deterministic projection of paid and zero-value subscription time."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, localcontext
from typing import Mapping, Sequence


@dataclass(frozen=True)
class EntitlementEvent:
    id: int
    user_id: int
    source_type: str
    source_id: str
    entry_type: str
    hours_delta: int
    created_at: datetime
    reversed_entry_id: int | None = None


@dataclass(frozen=True)
class LedgerEntry:
    id: int
    user_id: int
    entry_type: str
    paid_hours_delta: int
    paid_value_rub_delta: Decimal
    currency: str
    tariff_version_id: int
    payment_id: int | None
    reversal_of_id: int | None = None


@dataclass(frozen=True)
class PaymentSnapshot:
    id: int
    user_id: int
    tariff_id: int
    tariff_version_id: int | None
    amount: Decimal
    currency: str
    snapshot_duration_hours: int | None
    snapshot_amount: Decimal | None
    snapshot_currency: str | None


@dataclass(frozen=True)
class TariffVersionSnapshot:
    id: int
    tariff_id: int
    duration_hours: int
    price_rub: Decimal
    currency: str


@dataclass(frozen=True)
class ProjectedPaidLot:
    entitlement_entry_id: int
    paid_value_ledger_entry_id: int
    payment_id: int
    tariff_version_id: int
    original_paid_hours: int
    original_paid_value_rub: Decimal
    remaining_whole_hours: int
    remaining_paid_value_rub: Decimal
    segment_start: datetime
    segment_end: datetime


@dataclass(frozen=True)
class ProjectedBonusLot:
    entitlement_entry_id: int
    source_type: str
    source_id: str
    bonus_type: str
    original_hours: int
    remaining_whole_hours: int
    segment_start: datetime
    segment_end: datetime
    paid_value_rub: Decimal = Decimal("0")


@dataclass(frozen=True)
class SubscriptionBalanceSnapshot:
    as_of: datetime
    tracked: bool
    failure_code: str | None
    coverage_end: datetime | None
    remaining_paid_hours: int
    remaining_paid_value_rub: Decimal | None
    remaining_bonus_hours: int
    rounding_loss_hours: Decimal
    paid_lots: tuple[ProjectedPaidLot, ...]
    bonus_lots: tuple[ProjectedBonusLot, ...]
    source_ledger_entry_ids: tuple[int, ...]
    source_entitlement_entry_ids: tuple[int, ...]


@dataclass
class _Segment:
    event: EntitlementEvent
    start: datetime
    end: datetime
    ledger: LedgerEntry | None
    is_paid: bool


def _failed(
    as_of: datetime,
    code: str,
    events: Sequence[EntitlementEvent],
    ledger: Sequence[LedgerEntry],
    coverage_end=None,
):
    return SubscriptionBalanceSnapshot(
        as_of,
        False,
        code,
        coverage_end,
        0,
        None,
        0,
        Decimal(0),
        (),
        (),
        tuple(x.id for x in ledger),
        tuple(x.id for x in events),
    )


def project_subscription_balance(
    *,
    as_of: datetime,
    subscription_end: datetime | None,
    entitlement_events: Sequence[EntitlementEvent],
    ledger_entries: Sequence[LedgerEntry],
    tariff_versions: Mapping[int, TariffVersionSnapshot],
    payments: Mapping[int, PaymentSnapshot],
) -> SubscriptionBalanceSnapshot:
    """Replay source-attributed append-only history without infrastructure dependencies."""
    events = tuple(sorted(entitlement_events, key=lambda e: (e.created_at, e.id)))
    ledger = tuple(ledger_entries)

    def fail(code, end=None):
        return _failed(as_of, code, events, ledger, end)

    grants_types = {
        "payment_grant",
        "referral_user_bonus",
        "referral_referrer_bonus",
        "manual_grant",
    }
    reversal_types = {"payment_reversal", "referral_reversal"}
    if any(
        not (
            (
                event.entry_type in grants_types
                and event.hours_delta > 0
                and event.reversed_entry_id is None
            )
            or (
                event.entry_type in reversal_types
                and event.hours_delta < 0
                and event.reversed_entry_id is not None
            )
        )
        for event in events
    ):
        return fail("invalid_entitlement_shape")
    active = subscription_end is not None and subscription_end > as_of
    confirmed = [x for x in ledger if x.entry_type == "confirmed_payment"]
    ledger_reversals = [x for x in ledger if x.entry_type == "payment_reversal"]
    if any(
        x.entry_type not in {"confirmed_payment", "payment_reversal"}
        and (x.paid_hours_delta or x.paid_value_rub_delta)
        for x in ledger
    ):
        return fail("unsupported_paid_value_entry")
    grants = [
        e for e in events if e.entry_type == "payment_grant" and e.hours_delta > 0
    ]
    for item in confirmed:
        matches = [
            e
            for e in grants
            if e.source_type == "payment" and e.source_id == str(item.payment_id)
        ]
        if len(matches) != 1:
            return fail("confirmed_payment_without_entitlement_grant")
    by_id = {e.id: e for e in events}
    segments = []
    coverage = None
    used_confirmed = set()
    legacy_failure = None
    groups = {}
    for e in events:
        if e.entry_type in {"payment_reversal", "referral_reversal"}:
            groups.setdefault((e.source_type, e.source_id), []).append(e)
    processed = set()
    reversed_confirmed = set()
    used_ledger_reversals = set()
    for event in events:
        if event.hours_delta > 0:
            if event.entry_type not in {
                "payment_grant",
                "referral_user_bonus",
                "referral_referrer_bonus",
                "manual_grant",
            }:
                continue
            match = None
            if event.entry_type == "payment_grant":
                matches = [
                    x
                    for x in confirmed
                    if event.source_type == "payment"
                    and str(x.payment_id) == event.source_id
                ]
                if len(matches) != 1:
                    legacy_failure = "paid_grant_without_value_ledger"
                else:
                    match = matches[0]
                    if match.id in used_confirmed:
                        return fail(
                            "confirmed_payment_without_entitlement_grant", coverage
                        )
                    used_confirmed.add(match.id)
                    payment = payments.get(match.payment_id)
                    version = tariff_versions.get(match.tariff_version_id)
                    ok = (
                        match.user_id == event.user_id
                        and match.paid_hours_delta == event.hours_delta
                        and match.paid_value_rub_delta > 0
                        and match.currency == "RUB"
                        and payment is not None
                        and payment.user_id == event.user_id
                        and payment.tariff_version_id == match.tariff_version_id
                        and version is not None
                        and payment.tariff_id == version.tariff_id
                        and payment.currency
                        == payment.snapshot_currency
                        == version.currency
                        == match.currency
                        and payment.snapshot_duration_hours
                        == version.duration_hours
                        == event.hours_delta
                        and payment.amount
                        == payment.snapshot_amount
                        == version.price_rub
                        == match.paid_value_rub_delta
                    )
                    if not ok:
                        legacy_failure = "paid_grant_without_value_ledger"
            start = max(event.created_at, coverage) if coverage else event.created_at
            end = start + timedelta(hours=event.hours_delta)
            segments.append(
                _Segment(event, start, end, match, event.entry_type == "payment_grant")
            )
            coverage = end
            continue
        if event.entry_type not in {"payment_reversal", "referral_reversal"}:
            continue
        key = (event.source_type, event.source_id)
        if key in processed:
            continue
        processed.add(key)
        batch = sorted(groups[key], key=lambda e: (e.created_at, e.id))
        batch_time = batch[0].created_at
        originals = []
        for rev in batch:
            original = (
                by_id.get(rev.reversed_entry_id) if rev.reversed_entry_id else None
            )
            if original is None:
                return fail("reversal_source_missing", coverage)
            if len([x for x in batch if x.reversed_entry_id == original.id]) != 1:
                return fail("reversal_source_ambiguous", coverage)
            if rev.user_id != original.user_id or (rev.source_type, rev.source_id) != (
                original.source_type,
                original.source_id,
            ):
                return fail("reversal_source_mismatch", coverage)
            if rev.hours_delta != -original.hours_delta:
                return fail("reversal_amount_mismatch", coverage)
            originals.append(original)
            if rev.entry_type == "payment_reversal":
                if (
                    rev.source_type != "payment"
                    or original.entry_type != "payment_grant"
                ):
                    return fail("reversal_source_mismatch", coverage)
                source = [s for s in segments if s.event.id == original.id]
                if len(source) != 1 or source[0].ledger is None:
                    return fail("reversal_source_ambiguous", coverage)
                positive = source[0].ledger
                matches = [
                    x for x in ledger_reversals if x.reversal_of_id == positive.id
                ]
                if len(matches) != 1:
                    return fail("paid_reversal_without_ledger_reversal", coverage)
                lr = matches[0]
                if not (
                    lr.payment_id == positive.payment_id
                    and lr.user_id == positive.user_id
                    and lr.paid_hours_delta == -positive.paid_hours_delta
                    and lr.paid_value_rub_delta == -positive.paid_value_rub_delta
                    and lr.currency == positive.currency
                    and lr.tariff_version_id == positive.tariff_version_id
                ):
                    return fail("reversal_amount_mismatch", coverage)
                reversed_confirmed.add(positive.id)
                used_ledger_reversals.add(lr.id)
            elif rev.entry_type != "referral_reversal" or original.entry_type not in {
                "referral_user_bonus",
                "referral_referrer_bonus",
            }:
                return fail("reversal_source_mismatch", coverage)
        allowed = {o.id for o in originals}
        remove = sum(abs(x.hours_delta) for x in batch)
        available = sum(
            _whole(max(s.start, batch_time), s.end, batch_time, s.start)
            for s in segments
        )
        if remove > available:
            return fail("reversal_exceeds_balance", coverage)
        while remove:
            s = segments[-1]
            removable = _whole(max(s.start, batch_time), s.end, batch_time, s.start)
            cut = min(remove, removable)
            if not cut:
                return fail("reversal_exceeds_balance", coverage)
            if s.event.id not in allowed:
                return fail("reversal_crosses_unrelated_segments", coverage)
            s.end -= timedelta(hours=cut)
            remove -= cut
            if s.end <= s.start:
                segments.pop()
        if any(s.event.id in allowed for s in segments):
            if any(s.ledger and s.ledger.id in reversed_confirmed for s in segments):
                return fail("reversed_paid_lot_still_remaining", coverage)
            return fail("unrelated_paid_lot_trimmed", coverage)
        coverage = segments[-1].end if segments else batch_time
    if any(x.id not in used_ledger_reversals for x in ledger_reversals):
        return fail("ledger_reversal_without_entitlement_reversal", coverage)
    paid = []
    bonus = []
    loss = Decimal(0)
    untracked = 0
    for s in segments:
        whole, part = _remaining(s.start, s.end, as_of)
        loss += part
        if s.is_paid:
            if s.ledger is None:
                untracked += whole
                continue
            with localcontext() as ctx:
                ctx.prec = 38
                value = (
                    s.ledger.paid_value_rub_delta
                    * Decimal(whole)
                    / Decimal(s.ledger.paid_hours_delta)
                )
            paid.append(
                ProjectedPaidLot(
                    s.event.id,
                    s.ledger.id,
                    s.ledger.payment_id,
                    s.ledger.tariff_version_id,
                    s.ledger.paid_hours_delta,
                    s.ledger.paid_value_rub_delta,
                    whole,
                    value,
                    s.start,
                    s.end,
                )
            )
        else:
            bonus.append(
                ProjectedBonusLot(
                    s.event.id,
                    s.event.source_type,
                    s.event.source_id,
                    s.event.entry_type,
                    s.event.hours_delta,
                    whole,
                    s.start,
                    s.end,
                )
            )
    ph = sum(x.remaining_whole_hours for x in paid)
    bh = sum(x.remaining_whole_hours for x in bonus)
    if legacy_failure:
        if active:
            return fail(legacy_failure, coverage)
        if untracked or ph or bh:
            return fail("subscription_end_projection_mismatch", coverage)
        return SubscriptionBalanceSnapshot(
            as_of,
            True,
            None,
            subscription_end,
            0,
            Decimal(0),
            0,
            Decimal(0),
            (),
            (),
            tuple(x.id for x in ledger),
            tuple(x.id for x in events),
        )
    if not 0 <= loss < 1:
        return fail("rounding_invariant_violation", coverage)
    if active and (
        coverage is None or abs(_micros(coverage - subscription_end)) > 1_000_000
    ):
        return fail("subscription_end_projection_mismatch", coverage)
    if not active and (ph or bh):
        return fail("subscription_end_projection_mismatch", coverage)
    value = sum((x.remaining_paid_value_rub for x in paid), Decimal(0))
    ceiling = sum(
        (x.paid_value_rub_delta for x in confirmed if x.id not in reversed_confirmed),
        Decimal(0),
    )
    if not 0 <= value <= ceiling:
        return fail("reversed_paid_lot_still_remaining", coverage)
    return SubscriptionBalanceSnapshot(
        as_of,
        True,
        None,
        coverage,
        ph,
        value,
        bh,
        loss,
        tuple(paid),
        tuple(bonus),
        tuple(x.id for x in ledger),
        tuple(x.id for x in events),
    )


_HOUR_US = 3_600_000_000


def _micros(delta: timedelta) -> int:
    return ((delta.days * 86400 + delta.seconds) * 1_000_000) + delta.microseconds


def _whole(start, end, batch=None, segment_start=None):
    if (
        batch is not None
        and segment_start is not None
        and 0 <= _micros(batch - segment_start) <= 1_000_000
    ):
        start = segment_start
    return max(0, _micros(end - start) // _HOUR_US)


def _remaining(start, end, as_of):
    whole, remainder = divmod(max(0, _micros(end - max(as_of, start))), _HOUR_US)
    return whole, Decimal(remainder) / Decimal(_HOUR_US)
