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


def _failed(as_of: datetime, code: str, events: Sequence[EntitlementEvent], ledger: Sequence[LedgerEntry], coverage_end=None):
    return SubscriptionBalanceSnapshot(as_of, False, code, coverage_end, 0, None, 0,
        Decimal(0), (), (), tuple(x.id for x in ledger), tuple(x.id for x in events))


def project_subscription_balance(*, as_of: datetime, subscription_end: datetime | None,
        entitlement_events: Sequence[EntitlementEvent], ledger_entries: Sequence[LedgerEntry],
        tariff_versions: Mapping[int, TariffVersionSnapshot], payments: Mapping[int, PaymentSnapshot]) -> SubscriptionBalanceSnapshot:
    """Replay append-only history. Inputs are values only; no infrastructure is used."""
    events = tuple(sorted(entitlement_events, key=lambda x: (x.created_at, x.id)))
    ledger = tuple(ledger_entries)
    active = subscription_end is not None and subscription_end > as_of
    def expired_legacy_zero():
        return SubscriptionBalanceSnapshot(as_of, True, None, subscription_end, 0, Decimal(0), 0,
            Decimal(0), (), (), tuple(x.id for x in ledger), tuple(x.id for x in events))

    segments: list[_Segment] = []
    by_event = {e.id: e for e in events}
    used_reversals: set[int] = set()
    coverage: datetime | None = None
    confirmed = [x for x in ledger if x.entry_type == "confirmed_payment"]
    ledger_reversals = [x for x in ledger if x.entry_type == "payment_reversal"]

    for event in events:
        if event.hours_delta > 0:
            if event.entry_type not in {"payment_grant", "referral_user_bonus", "referral_referrer_bonus", "manual_grant"}:
                continue
            match = None
            if event.entry_type == "payment_grant":
                candidates = [x for x in confirmed if x.payment_id is not None and str(x.payment_id) == event.source_id]
                if len(candidates) != 1:
                    if not active:
                        return expired_legacy_zero()
                    return _failed(as_of, "paid_grant_without_value_ledger", events, ledger, coverage)
                match = candidates[0]
                payment = payments.get(match.payment_id) if match.payment_id is not None else None
                version = tariff_versions.get(match.tariff_version_id)
                compatible = (match.user_id == event.user_id and match.paid_hours_delta == event.hours_delta
                    and match.paid_value_rub_delta > 0 and match.currency == "RUB" and payment is not None
                    and payment.id == match.payment_id and payment.user_id == event.user_id
                    and payment.tariff_version_id == match.tariff_version_id and version is not None
                    and payment.tariff_id == version.tariff_id and payment.currency == "RUB"
                    and payment.snapshot_currency == "RUB" and payment.snapshot_duration_hours == event.hours_delta
                    and payment.amount == match.paid_value_rub_delta
                    and payment.snapshot_amount == match.paid_value_rub_delta
                    and version.price_rub == match.paid_value_rub_delta
                    and version.duration_hours == event.hours_delta and version.currency == "RUB")
                if not compatible:
                    if not active:
                        return expired_legacy_zero()
                    return _failed(as_of, "paid_grant_without_value_ledger", events, ledger, coverage)
            start = max(event.created_at, coverage) if coverage else event.created_at
            end = start + timedelta(hours=event.hours_delta)
            segments.append(_Segment(event, start, end, match)); coverage = end
            continue

        if event.entry_type not in {"payment_reversal", "referral_reversal"}:
            continue
        original = by_event.get(event.reversed_entry_id) if event.reversed_entry_id else None
        if original is None:
            return _failed(as_of, "reversal_source_missing", events, ledger, coverage)
        related = [x for x in events if x.reversed_entry_id == original.id and x.entry_type == event.entry_type]
        if len(related) != 1 or original.user_id != event.user_id:
            return _failed(as_of, "reversal_source_ambiguous", events, ledger, coverage)
        if event.entry_type == "referral_reversal" and original.entry_type not in {"referral_user_bonus", "referral_referrer_bonus"}:
            return _failed(as_of, "reversal_source_ambiguous", events, ledger, coverage)
        if event.entry_type == "payment_reversal":
            source_segments = [s for s in segments if s.event.id == original.id and s.ledger]
            if len(source_segments) != 1:
                return _failed(as_of, "reversal_source_ambiguous", events, ledger, coverage)
            reversals = [x for x in ledger_reversals if x.reversal_of_id == source_segments[0].ledger.id]
            reversal = reversals[0] if len(reversals) == 1 else None
            if (reversal is None or reversal.user_id != event.user_id
                    or reversal.payment_id != source_segments[0].ledger.payment_id
                    or reversal.paid_hours_delta != -source_segments[0].ledger.paid_hours_delta
                    or reversal.paid_value_rub_delta != -source_segments[0].ledger.paid_value_rub_delta):
                return _failed(as_of, "paid_reversal_without_ledger_reversal", events, ledger, coverage)
            used_reversals.add(reversal.id)
        remove = abs(event.hours_delta)
        available = sum(max(0, int((s.end - max(s.start, event.created_at)).total_seconds() // 3600)) for s in segments)
        if remove > available:
            return _failed(as_of, "reversal_exceeds_balance", events, ledger, coverage)
        while remove:
            s = segments[-1]
            removable = max(0, int((s.end - max(s.start, event.created_at)).total_seconds() // 3600))
            cut = min(remove, removable)
            if cut:
                s.end -= timedelta(hours=cut); remove -= cut
            if s.end <= s.start:
                segments.pop()
            elif not cut:
                return _failed(as_of, "reversal_exceeds_balance", events, ledger, coverage)
        coverage = segments[-1].end if segments else event.created_at

    if any(x.id not in used_reversals for x in ledger_reversals):
        return _failed(as_of, "ledger_reversal_without_entitlement_reversal", events, ledger, coverage)
    if active and (coverage is None or abs((coverage - subscription_end).total_seconds()) > 1):
        return _failed(as_of, "subscription_end_projection_mismatch", events, ledger, coverage)

    paid_lots=[]; bonus_lots=[]; loss=Decimal(0)
    for s in segments:
        seconds = max(0.0, (s.end - max(as_of, s.start)).total_seconds())
        whole = int(seconds // 3600)
        loss += Decimal(str((seconds / 3600) - whole))
        if s.ledger:
            with localcontext() as ctx:
                ctx.prec = 38
                value = s.ledger.paid_value_rub_delta * Decimal(whole) / Decimal(s.ledger.paid_hours_delta)
            paid_lots.append(ProjectedPaidLot(s.event.id, s.ledger.id, s.ledger.payment_id,
                s.ledger.tariff_version_id, s.ledger.paid_hours_delta, s.ledger.paid_value_rub_delta,
                whole, value, s.start, s.end))
        else:
            bonus_lots.append(ProjectedBonusLot(s.event.id, s.event.source_type, s.event.source_id,
                s.event.entry_type, s.event.hours_delta, whole, s.start, s.end))
    # Only the currently-running segment can lose a fractional hour.
    if loss >= 1:
        loss = loss % 1
    total_value=sum((x.remaining_paid_value_rub for x in paid_lots), Decimal(0))
    return SubscriptionBalanceSnapshot(as_of, True, None, coverage, sum(x.remaining_whole_hours for x in paid_lots),
        total_value, sum(x.remaining_whole_hours for x in bonus_lots), loss, tuple(paid_lots), tuple(bonus_lots),
        tuple(x.id for x in ledger), tuple(x.id for x in events))
