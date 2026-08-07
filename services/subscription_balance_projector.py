"""Pure projection of subscription time purchased from the internal balance."""

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
    quote_id: int | None = None
    metadata: Mapping[str, object] | None = None
    created_at: datetime | None = None


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


def _failed(as_of, code, events, ledger, coverage_end=None):
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
        tuple(item.id for item in ledger),
        tuple(item.id for item in events),
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
) -> SubscriptionBalanceSnapshot:
    """Replay only account purchases, tariff changes and zero-value bonuses."""
    events = tuple(sorted(entitlement_events, key=lambda item: (item.created_at, item.id)))
    ledger = tuple(ledger_entries)

    def fail(code, end=None):
        return _failed(as_of, code, events, ledger, end)

    if subscription_end is not None and not events and not ledger:
        return fail("subscription_balance_untracked")

    grants = {
        "account_purchase_grant",
        "referral_user_bonus",
        "referral_referrer_bonus",
        "manual_grant",
        "tariff_change",
    }
    if any(
        not (
            event.entry_type in grants
            and event.hours_delta >= 0
            and event.reversed_entry_id is None
        )
        and not (
            event.entry_type == "referral_reversal"
            and event.hours_delta < 0
            and event.reversed_entry_id is not None
        )
        for event in events
    ):
        return fail("invalid_entitlement_shape")

    purchases = [item for item in ledger if item.entry_type == "account_purchase"]
    conversions = [item for item in ledger if item.entry_type == "tariff_conversion"]
    if any(
        item.entry_type not in {"account_purchase", "tariff_conversion"}
        and (item.paid_hours_delta or item.paid_value_rub_delta)
        for item in ledger
    ):
        return fail("unsupported_paid_value_entry")

    purchase_grants = [
        event
        for event in events
        if event.entry_type == "account_purchase_grant" and event.hours_delta > 0
    ]
    for purchase in purchases:
        matching = [
            event
            for event in purchase_grants
            if event.source_type == "quote"
            and event.source_id == str(purchase.quote_id)
        ]
        if len(matching) != 1:
            return fail("account_purchase_without_entitlement_grant")

    by_id = {event.id: event for event in events}
    segments: list[_Segment] = []
    coverage = None
    used_purchases: set[int] = set()
    used_conversions: set[int] = set()
    reversal_groups: dict[tuple[str, str], list[EntitlementEvent]] = {}
    for event in events:
        if event.entry_type == "referral_reversal":
            reversal_groups.setdefault((event.source_type, event.source_id), []).append(event)
    processed_reversals: set[tuple[str, str]] = set()

    for event in events:
        if event.entry_type == "tariff_change":
            metadata = event.metadata or {}
            matching = [
                item
                for item in conversions
                if item.user_id == event.user_id
                and item.quote_id is not None
                and event.source_type == "quote"
                and str(item.quote_id) == event.source_id
            ]
            if len(matching) != 1 or matching[0].id in used_conversions:
                return fail("tariff_change_conversion_missing", coverage)
            conversion = matching[0]
            try:
                current_paid_hours = _metadata_int(metadata, "current_paid_hours")
                current_paid_value = _metadata_decimal(metadata, "current_paid_value_rub")
                resulting_paid_hours = _metadata_int(metadata, "resulting_paid_hours")
                resulting_paid_value = _metadata_decimal(metadata, "resulting_paid_value_rub")
                resulting_bonus_hours = _metadata_int(metadata, "resulting_bonus_hours")
                current_bonus_hours = _metadata_int(metadata, "current_bonus_hours")
                anchor = _metadata_datetime(metadata, "balance_as_of")
                source_entitlement_ids = _metadata_ids(
                    metadata, "source_entitlement_entry_ids"
                )
                source_ledger_ids = _metadata_ids(metadata, "source_ledger_entry_ids")
            except (KeyError, TypeError, ValueError, ArithmeticError):
                return fail("tariff_change_metadata_invalid", coverage)
            if (
                conversion.metadata != metadata
                or conversion.tariff_version_id not in tariff_versions
                or conversion.paid_hours_delta != resulting_paid_hours - current_paid_hours
                or conversion.paid_value_rub_delta != resulting_paid_value - current_paid_value
                or event.hours_delta != resulting_paid_hours + resulting_bonus_hours
                or event.id in source_entitlement_ids
                or conversion.id in source_ledger_ids
                or not source_entitlement_ids.issubset(by_id)
                or not source_ledger_ids.issubset({item.id for item in ledger})
                or anchor > event.created_at
            ):
                return fail("tariff_change_metadata_mismatch", coverage)

            source_paid_hours = 0
            source_paid_value = Decimal(0)
            source_bonus_hours = 0
            for segment in segments:
                whole, _ = _remaining(segment.start, segment.end, anchor)
                if segment.is_paid:
                    if segment.ledger is None:
                        return fail("tariff_change_source_invalid", coverage)
                    original_hours = segment.original_paid_hours or segment.ledger.paid_hours_delta
                    original_value = (
                        segment.original_paid_value_rub
                        if segment.original_paid_value_rub is not None
                        else segment.ledger.paid_value_rub_delta
                    )
                    if original_hours <= 0 or original_value < 0:
                        return fail("tariff_change_source_invalid", coverage)
                    source_paid_hours += whole
                    source_paid_value += original_value * Decimal(whole) / Decimal(
                        original_hours
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
            paid_end = anchor + timedelta(hours=resulting_paid_hours)
            if resulting_paid_hours:
                segments.append(
                    _Segment(
                        EntitlementEvent(
                            event.id,
                            event.user_id,
                            event.source_type,
                            event.source_id,
                            "tariff_change_paid",
                            resulting_paid_hours,
                            anchor,
                            metadata=metadata,
                        ),
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
                bonus_end = paid_end + timedelta(hours=resulting_bonus_hours)
                segments.append(
                    _Segment(
                        EntitlementEvent(
                            event.id,
                            event.user_id,
                            event.source_type,
                            event.source_id,
                            "tariff_change_bonus",
                            resulting_bonus_hours,
                            paid_end,
                            metadata=metadata,
                        ),
                        paid_end,
                        bonus_end,
                        None,
                        False,
                    )
                )
                coverage = bonus_end
            used_conversions.add(conversion.id)
            continue

        if event.hours_delta > 0:
            ledger_entry = None
            is_paid = event.entry_type == "account_purchase_grant"
            if is_paid:
                matching = [
                    item
                    for item in purchases
                    if event.source_type == "quote"
                    and event.source_id == str(item.quote_id)
                ]
                if len(matching) != 1:
                    return fail("account_purchase_without_value_ledger", coverage)
                ledger_entry = matching[0]
                version = tariff_versions.get(ledger_entry.tariff_version_id)
                if (
                    ledger_entry.id in used_purchases
                    or ledger_entry.user_id != event.user_id
                    or ledger_entry.paid_hours_delta != event.hours_delta
                    or ledger_entry.paid_value_rub_delta <= 0
                    or ledger_entry.currency != "RUB"
                    or version is None
                    or version.currency != "RUB"
                    or version.duration_hours != event.hours_delta
                    or version.price_rub != ledger_entry.paid_value_rub_delta
                ):
                    return fail("account_purchase_value_mismatch", coverage)
                used_purchases.add(ledger_entry.id)
            start = max(event.created_at, coverage) if coverage else event.created_at
            end = start + timedelta(hours=event.hours_delta)
            segments.append(_Segment(event, start, end, ledger_entry, is_paid))
            coverage = end
            continue

        if event.entry_type != "referral_reversal":
            continue
        key = (event.source_type, event.source_id)
        if key in processed_reversals:
            continue
        processed_reversals.add(key)
        batch = sorted(reversal_groups[key], key=lambda item: (item.created_at, item.id))
        batch_time = batch[0].created_at
        originals: list[EntitlementEvent] = []
        for reversal in batch:
            original = by_id.get(reversal.reversed_entry_id or -1)
            if original is None:
                return fail("reversal_source_missing", coverage)
            if len([item for item in batch if item.reversed_entry_id == original.id]) != 1:
                return fail("reversal_source_ambiguous", coverage)
            if (
                reversal.user_id != original.user_id
                or (reversal.source_type, reversal.source_id)
                != (original.source_type, original.source_id)
                or reversal.hours_delta != -original.hours_delta
                or original.entry_type
                not in {"referral_user_bonus", "referral_referrer_bonus"}
            ):
                return fail("reversal_source_mismatch", coverage)
            originals.append(original)
        allowed = {item.id for item in originals}
        remove = sum(abs(item.hours_delta) for item in batch)
        available = sum(
            _whole(max(segment.start, batch_time), segment.end, batch_time, segment.start)
            for segment in segments
        )
        if remove > available:
            return fail("reversal_exceeds_balance", coverage)
        while remove:
            segment = segments[-1]
            removable = _whole(
                max(segment.start, batch_time),
                segment.end,
                batch_time,
                segment.start,
            )
            cut = min(remove, removable)
            if not cut or segment.event.id not in allowed:
                return fail("reversal_crosses_unrelated_segments", coverage)
            segment.end -= timedelta(hours=cut)
            remove -= cut
            if segment.end <= segment.start:
                segments.pop()
        if any(segment.event.id in allowed for segment in segments):
            return fail("reversal_source_still_remaining", coverage)
        coverage = segments[-1].end if segments else batch_time

    if any(item.id not in used_purchases for item in purchases):
        return fail("account_purchase_without_entitlement_grant", coverage)
    if any(item.id not in used_conversions for item in conversions):
        return fail("tariff_conversion_without_entitlement", coverage)

    paid: list[ProjectedPaidLot] = []
    bonus: list[ProjectedBonusLot] = []
    rounding_loss = Decimal(0)
    for segment in segments:
        whole, fraction = _remaining(segment.start, segment.end, as_of)
        rounding_loss += fraction
        if segment.is_paid:
            if segment.ledger is None:
                return fail("account_purchase_without_value_ledger", coverage)
            original_hours = segment.original_paid_hours or segment.ledger.paid_hours_delta
            original_value = (
                segment.original_paid_value_rub
                if segment.original_paid_value_rub is not None
                else segment.ledger.paid_value_rub_delta
            )
            if original_hours <= 0 or original_value < 0:
                return fail("paid_lot_value_invalid", coverage)
            with localcontext() as context:
                context.prec = 38
                remaining_value = (
                    original_value * Decimal(whole) / Decimal(original_hours)
                )
            paid.append(
                ProjectedPaidLot(
                    segment.event.id,
                    segment.ledger.id,
                    segment.ledger.tariff_version_id,
                    original_hours,
                    original_value,
                    whole,
                    remaining_value,
                    segment.start,
                    segment.end,
                    segment.ledger.quote_id,
                )
            )
        else:
            bonus.append(
                ProjectedBonusLot(
                    segment.event.id,
                    segment.event.source_type,
                    segment.event.source_id,
                    segment.event.entry_type,
                    segment.event.hours_delta,
                    whole,
                    segment.start,
                    segment.end,
                )
            )

    paid_hours = sum(item.remaining_whole_hours for item in paid)
    bonus_hours = sum(item.remaining_whole_hours for item in bonus)
    active = subscription_end is not None and subscription_end > as_of
    if not Decimal(0) <= rounding_loss < Decimal(1):
        return fail("rounding_invariant_violation", coverage)
    if active and (
        coverage is None or abs(_micros(coverage - subscription_end)) > 1_000_000
    ):
        if coverage is None or coverage < subscription_end:
            untracked_start = max(coverage, as_of) if coverage else as_of
            untracked_whole = _whole(untracked_start, subscription_end, as_of, coverage)
            if untracked_whole > 0:
                bonus_hours += untracked_whole
                bonus.append(
                    ProjectedBonusLot(
                        0,
                        "legacy",
                        "admin",
                        "manual_grant",
                        untracked_whole,
                        untracked_whole,
                        untracked_start,
                        subscription_end,
                    )
                )
            coverage = subscription_end

        if coverage is None or abs(_micros(coverage - subscription_end)) > 1_000_000:
            return fail("subscription_end_projection_mismatch", coverage)
    if not active and (paid_hours or bonus_hours):
        return fail("subscription_end_projection_mismatch", coverage)
    value = sum((item.remaining_paid_value_rub for item in paid), Decimal(0))
    ceiling = sum((item.paid_value_rub_delta for item in purchases), Decimal(0)) + sum(
        (item.paid_value_rub_delta for item in conversions), Decimal(0)
    )
    if value < 0 or value > max(Decimal(0), ceiling):
        return fail("paid_value_projection_mismatch", coverage)
    return SubscriptionBalanceSnapshot(
        as_of,
        True,
        None,
        coverage,
        paid_hours,
        value,
        bonus_hours,
        rounding_loss,
        tuple(paid),
        tuple(bonus),
        tuple(item.id for item in ledger),
        tuple(item.id for item in events),
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
