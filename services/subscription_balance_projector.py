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
    metadata: Mapping[str, object] | None = None


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
    quote_id: int | None = None
    metadata: Mapping[str, object] | None = None
    created_at: datetime | None = None


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
    payment_id: int | None
    tariff_version_id: int
    original_paid_hours: int
    original_paid_value_rub: Decimal
    remaining_whole_hours: int
    remaining_paid_value_rub: Decimal
    segment_start: datetime
    segment_end: datetime
    quote_id: int | None = None


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
    original_paid_hours: int | None = None
    original_paid_value_rub: Decimal | None = None


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


def _metadata_int(metadata: Mapping[str, object], key: str) -> int:
    value = metadata.get(key)
    if isinstance(value, bool):
        raise ValueError(key)
    result = int(value)
    if result < 0 or str(result) != str(value):
        raise ValueError(key)
    return result


def _metadata_decimal(metadata: Mapping[str, object], key: str) -> Decimal:
    result = Decimal(str(metadata.get(key)))
    if not result.is_finite() or result < 0:
        raise ValueError(key)
    return result


def _metadata_datetime(metadata: Mapping[str, object], key: str) -> datetime:
    raw = metadata.get(key)
    if not isinstance(raw, str):
        raise ValueError(key)
    result = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError(key)
    return result


def _metadata_ids(metadata: Mapping[str, object], key: str) -> set[int]:
    values = metadata.get(key)
    if not isinstance(values, list) or any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in values
    ):
        raise ValueError(key)
    result = set(values)
    if len(result) != len(values):
        raise ValueError(key)
    return result


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
        "account_purchase_grant",
        "referral_user_bonus",
        "referral_referrer_bonus",
        "manual_grant",
        "tariff_change",
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
    confirmed = [
        x for x in ledger if x.entry_type in {"confirmed_payment", "account_purchase"}
    ]
    conversions = [x for x in ledger if x.entry_type == "tariff_conversion"]
    ledger_reversals = [x for x in ledger if x.entry_type == "payment_reversal"]
    if any(
        x.entry_type
        not in {
            "confirmed_payment",
            "account_purchase",
            "tariff_conversion",
            "payment_reversal",
        }
        and (x.paid_hours_delta or x.paid_value_rub_delta)
        for x in ledger
    ):
        return fail("unsupported_paid_value_entry")
    grants = [
        e
        for e in events
        if e.entry_type in {"payment_grant", "account_purchase_grant"}
        and e.hours_delta > 0
    ]
    for item in confirmed:
        matches = [
            e
            for e in grants
            if (
                item.entry_type == "confirmed_payment"
                and e.entry_type == "payment_grant"
                and e.source_type == "payment"
                and e.source_id == str(item.payment_id)
            )
            or (
                item.entry_type == "account_purchase"
                and e.entry_type == "account_purchase_grant"
                and e.source_type == "quote"
                and e.source_id == str(item.quote_id)
            )
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
    used_conversions = set()
    for event in events:
        if event.entry_type == "tariff_change":
            metadata = event.metadata or {}
            matches = [
                row
                for row in conversions
                if row.user_id == event.user_id
                and row.quote_id is not None
                and event.source_type == "quote"
                and str(row.quote_id) == event.source_id
            ]
            if len(matches) != 1 or matches[0].id in used_conversions:
                return fail("tariff_change_conversion_missing", coverage)
            conversion = matches[0]
            try:
                current_paid_hours = _metadata_int(metadata, "current_paid_hours")
                current_paid_value = _metadata_decimal(
                    metadata, "current_paid_value_rub"
                )
                resulting_paid_hours = _metadata_int(metadata, "resulting_paid_hours")
                resulting_paid_value = _metadata_decimal(
                    metadata, "resulting_paid_value_rub"
                )
                resulting_bonus_hours = _metadata_int(metadata, "resulting_bonus_hours")
                current_bonus_hours = _metadata_int(metadata, "current_bonus_hours")
                anchor = _metadata_datetime(metadata, "balance_as_of")
                source_entitlement_ids = _metadata_ids(
                    metadata, "source_entitlement_entry_ids"
                )
                source_ledger_ids = _metadata_ids(
                    metadata, "source_ledger_entry_ids"
                )
            except (KeyError, TypeError, ValueError, ArithmeticError):
                return fail("tariff_change_metadata_invalid", coverage)
            if (
                conversion.metadata != metadata
                or conversion.payment_id is not None
                or conversion.reversal_of_id is not None
                or conversion.tariff_version_id not in tariff_versions
                or conversion.paid_hours_delta
                != resulting_paid_hours - current_paid_hours
                or conversion.paid_value_rub_delta
                != resulting_paid_value - current_paid_value
                or event.hours_delta != resulting_paid_hours + resulting_bonus_hours
                or event.reversed_entry_id is not None
                or event.id in source_entitlement_ids
                or conversion.id in source_ledger_ids
                or not source_entitlement_ids.issubset(by_id)
                or not source_ledger_ids.issubset({row.id for row in ledger})
                or anchor > event.created_at
            ):
                return fail("tariff_change_metadata_mismatch", coverage)
            source_paid_hours = 0
            source_paid_value = Decimal(0)
            source_bonus_hours = 0
            for segment in segments:
                whole, _ = _remaining(segment.start, segment.end, anchor)
                if segment.is_paid:
                    original_hours = (
                        segment.original_paid_hours
                        if segment.original_paid_hours is not None
                        else segment.ledger.paid_hours_delta
                    )
                    original_value = (
                        segment.original_paid_value_rub
                        if segment.original_paid_value_rub is not None
                        else segment.ledger.paid_value_rub_delta
                    )
                    if original_hours <= 0 or original_value < 0:
                        return fail("tariff_change_source_invalid", coverage)
                    source_paid_hours += whole
                    source_paid_value += (
                        original_value * Decimal(whole) / Decimal(original_hours)
                    )
                else:
                    source_bonus_hours += whole
            if (
                source_paid_hours != current_paid_hours
                or source_paid_value != current_paid_value
                or source_bonus_hours != current_bonus_hours
            ):
                return fail("tariff_change_source_mismatch", coverage)
            segments = []
            paid_event = EntitlementEvent(
                event.id,
                event.user_id,
                event.source_type,
                event.source_id,
                "tariff_change_paid",
                resulting_paid_hours,
                anchor,
                metadata=metadata,
            )
            paid_end = anchor + timedelta(hours=resulting_paid_hours)
            if resulting_paid_hours:
                segments.append(
                    _Segment(
                        paid_event,
                        anchor,
                        paid_end,
                        conversion,
                        True,
                        resulting_paid_hours,
                        resulting_paid_value,
                    )
                )
            coverage = paid_end
            if resulting_bonus_hours:
                bonus_event = EntitlementEvent(
                    event.id,
                    event.user_id,
                    event.source_type,
                    event.source_id,
                    "tariff_change_bonus",
                    resulting_bonus_hours,
                    paid_end,
                    metadata=metadata,
                )
                coverage = paid_end + timedelta(hours=resulting_bonus_hours)
                segments.append(
                    _Segment(
                        bonus_event,
                        paid_end,
                        coverage,
                        None,
                        False,
                    )
                )
            used_conversions.add(conversion.id)
            continue
        if event.hours_delta > 0:
            if event.entry_type not in {
                "payment_grant",
                "account_purchase_grant",
                "referral_user_bonus",
                "referral_referrer_bonus",
                "manual_grant",
            }:
                continue
            match = None
            if event.entry_type in {"payment_grant", "account_purchase_grant"}:
                matches = [
                    x
                    for x in confirmed
                    if (
                        event.entry_type == "payment_grant"
                        and x.entry_type == "confirmed_payment"
                        and event.source_type == "payment"
                        and str(x.payment_id) == event.source_id
                    )
                    or (
                        event.entry_type == "account_purchase_grant"
                        and x.entry_type == "account_purchase"
                        and event.source_type == "quote"
                        and str(x.quote_id) == event.source_id
                    )
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
                    version = tariff_versions.get(match.tariff_version_id)
                    common_ok = (
                        match.user_id == event.user_id
                        and match.paid_hours_delta == event.hours_delta
                        and match.paid_value_rub_delta > 0
                        and match.currency == "RUB"
                        and version is not None
                        and version.currency == match.currency
                        and version.duration_hours == event.hours_delta
                    )
                    if match.entry_type == "confirmed_payment":
                        payment = payments.get(match.payment_id)
                        ok = (
                            common_ok
                            and payment is not None
                            and payment.user_id == event.user_id
                            and payment.tariff_version_id == match.tariff_version_id
                            and payment.tariff_id == version.tariff_id
                            and payment.currency
                            == payment.snapshot_currency
                            == match.currency
                            and payment.snapshot_duration_hours
                            == version.duration_hours
                            and payment.amount
                            == payment.snapshot_amount
                            == version.price_rub
                            == match.paid_value_rub_delta
                        )
                    else:
                        ok = (
                            common_ok
                            and match.payment_id is None
                            and match.quote_id is not None
                            and match.paid_value_rub_delta == version.price_rub
                        )
                    if not ok:
                        legacy_failure = "paid_grant_without_value_ledger"
            start = max(event.created_at, coverage) if coverage else event.created_at
            end = start + timedelta(hours=event.hours_delta)
            segments.append(
                _Segment(
                    event,
                    start,
                    end,
                    match,
                    event.entry_type in {"payment_grant", "account_purchase_grant"},
                )
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
    if any(x.id not in used_conversions for x in conversions):
        return fail("tariff_conversion_without_entitlement", coverage)
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
            original_hours = (
                s.original_paid_hours
                if s.original_paid_hours is not None
                else s.ledger.paid_hours_delta
            )
            original_value = (
                s.original_paid_value_rub
                if s.original_paid_value_rub is not None
                else s.ledger.paid_value_rub_delta
            )
            if original_hours <= 0 or original_value < 0:
                return fail("paid_lot_value_invalid", coverage)
            with localcontext() as ctx:
                ctx.prec = 38
                value = original_value * Decimal(whole) / Decimal(original_hours)
            paid.append(
                ProjectedPaidLot(
                    s.event.id,
                    s.ledger.id,
                    s.ledger.payment_id,
                    s.ledger.tariff_version_id,
                    original_hours,
                    original_value,
                    whole,
                    value,
                    s.start,
                    s.end,
                    s.ledger.quote_id,
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
    ) + sum((x.paid_value_rub_delta for x in conversions), Decimal(0))
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
