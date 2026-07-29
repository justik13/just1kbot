"""Pure, Decimal-only tariff value conversion.

This module deliberately knows nothing about persistence, Telegram or payment
providers.  Bonus time is carried separately and never enters the value pool.
"""
from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, localcontext


RUB = "RUB"


class TariffCalculationError(ValueError):
    """The supplied economic snapshots cannot be converted safely."""


@dataclass(frozen=True)
class TariffVersionSnapshot:
    tariff_id: int
    version_id: int | str
    duration_hours: int
    price_rub: Decimal
    currency: str = RUB


@dataclass(frozen=True)
class TariffCalculation:
    required_payment_rub: Decimal
    resulting_paid_hours: int
    retained_bonus_hours: int
    rounding_loss_hours: Decimal
    rounding_loss_value_rub: Decimal
    paid_value_before_rub: Decimal
    paid_value_after_rub: Decimal
    invariant_holds: bool
    reason_code: str


def _money(value, field: str) -> Decimal:
    if isinstance(value, float):
        raise TariffCalculationError(f"{field}: float is not supported")
    try:
        result = Decimal(value)
    except Exception as exc:
        raise TariffCalculationError(f"{field}: invalid Decimal") from exc
    if not result.is_finite() or result < 0:
        raise TariffCalculationError(f"{field}: must be finite and non-negative")
    return result


def calculate_tariff_value(
    *, source_paid_hours: int, source_paid_value_rub,
    source_tariff: TariffVersionSnapshot | None,
    target_tariff: TariffVersionSnapshot,
    confirmed_additional_payment_rub, bonus_hours: int,
    requested_duration_hours: int | None = None,
    bonus_value_rub=Decimal("0"),
) -> TariffCalculation:
    """Convert residual paid value to whole target-tariff hours, fail closed."""
    if not isinstance(source_paid_hours, int) or source_paid_hours < 0:
        raise TariffCalculationError("source_paid_hours must be a non-negative integer")
    if not isinstance(bonus_hours, int) or bonus_hours < 0:
        raise TariffCalculationError("bonus_hours must be a non-negative integer")
    if target_tariff.duration_hours <= 0 or (
        source_tariff and source_tariff.duration_hours <= 0
    ):
        raise TariffCalculationError("tariff duration must be positive")
    if target_tariff.currency != RUB or (
        source_tariff and source_tariff.currency != RUB
    ):
        raise TariffCalculationError("unsupported currency")
    target_price = _money(target_tariff.price_rub, "target price")
    if target_price <= 0:
        raise TariffCalculationError("target price must be positive")
    source_value = _money(source_paid_value_rub, "source paid value")
    payment = _money(confirmed_additional_payment_rub, "confirmed payment")
    if _money(bonus_value_rub, "bonus value") != 0:
        raise TariffCalculationError("bonus hours have zero monetary value")
    if source_tariff is None and (source_paid_hours or source_value):
        raise TariffCalculationError("source snapshot is required for paid balance")
    if source_tariff is not None:
        source_price = _money(source_tariff.price_rub, "source price")
        max_source_value = Decimal(source_paid_hours) * source_price / source_tariff.duration_hours
        if source_value > max_source_value:
            raise TariffCalculationError("source hours and value snapshots are incompatible")
    if requested_duration_hours is not None and requested_duration_hours != target_tariff.duration_hours:
        raise TariffCalculationError("arbitrary duration is not supported")

    required = max(Decimal("0"), target_price - source_value).quantize(
        Decimal("1"), rounding=ROUND_CEILING
    )
    pool = source_value + payment
    with localcontext() as context:
        context.prec = 50
        hourly_rate = target_price / Decimal(target_tariff.duration_hours)
        # Multiplication before division avoids a repeating hourly rate turning an
        # exact full tariff into N-1 hours at the floor boundary.
        exact_hours = pool * Decimal(target_tariff.duration_hours) / target_price
        whole_hours = int(exact_hours.to_integral_value(rounding=ROUND_FLOOR))
        paid_after = Decimal(whole_hours) * target_price / target_tariff.duration_hours
        loss_hours = exact_hours - Decimal(whole_hours)
        loss_value = pool - paid_after
    invariant = paid_after <= pool
    if not invariant or loss_hours < 0 or loss_hours >= 1 or loss_value < 0:
        raise TariffCalculationError("paid-value invariant violated")
    if source_tariff is None:
        reason = "purchase"
    elif source_tariff.tariff_id == target_tariff.tariff_id:
        reason = "renew"
    elif hourly_rate > _money(source_tariff.price_rub, "source price") / source_tariff.duration_hours:
        reason = "upgrade"
    else:
        reason = "downgrade"
    return TariffCalculation(
        required, whole_hours, bonus_hours, loss_hours, loss_value,
        source_value, paid_after, invariant, reason,
    )
