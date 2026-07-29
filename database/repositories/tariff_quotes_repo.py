"""Persistence boundary for immutable tariff versions and checkout quotes."""
import uuid
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Tariff, TariffQuote, TariffVersion
from utils.datetime_helpers import now_utc

QUOTE_LIFETIME = timedelta(minutes=15)


async def get_or_create_current_version(session: AsyncSession, tariff: Tariff) -> TariffVersion:
    # Serializes version allocation and prevents two distinct snapshots for one edit.
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": tariff.id})
    version = await session.scalar(
        select(TariffVersion).where(
            TariffVersion.tariff_id == tariff.id,
            TariffVersion.name_snapshot == tariff.name,
            TariffVersion.duration_hours == tariff.duration_days * 24,
            TariffVersion.device_limit == tariff.device_limit,
            TariffVersion.price_rub == Decimal(tariff.price_rub),
            TariffVersion.currency == "RUB",
        ).order_by(TariffVersion.version_number.desc()).limit(1)
    )
    if version:
        return version
    number = (await session.scalar(select(TariffVersion.version_number).where(
        TariffVersion.tariff_id == tariff.id
    ).order_by(TariffVersion.version_number.desc()).limit(1)) or 0) + 1
    version = TariffVersion(
        tariff_id=tariff.id, version_number=number, name_snapshot=tariff.name,
        duration_hours=tariff.duration_days * 24, device_limit=tariff.device_limit,
        price_rub=Decimal(tariff.price_rub), currency="RUB",
    )
    session.add(version)
    await session.flush()
    return version


async def expire_quotes(session: AsyncSession, user_id: int) -> None:
    now = now_utc()
    rows = (await session.scalars(select(TariffQuote).where(
        TariffQuote.user_id == user_id, TariffQuote.status == "active",
        TariffQuote.expires_at <= now,
    ).with_for_update())).all()
    for quote in rows:
        quote.status = "expired"


async def get_or_create_checkout_quote(
    session: AsyncSession, *, user_id: int, tariff: Tariff, operation_type: str,
) -> tuple[TariffQuote, TariffVersion]:
    if operation_type not in {"purchase", "renew"}:
        raise ValueError("checkout quote must be purchase or renew")
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": -user_id})
    await expire_quotes(session, user_id)
    version = await get_or_create_current_version(session, tariff)
    existing = await session.scalar(select(TariffQuote).where(
        TariffQuote.user_id == user_id,
        TariffQuote.target_tariff_version_id == version.id,
        TariffQuote.operation_type == operation_type,
        TariffQuote.status == "active",
    ))
    if existing:
        return existing, version
    now = now_utc()
    quote = TariffQuote(
        public_id=uuid.uuid4(), user_id=user_id, operation_type=operation_type,
        target_tariff_version_id=version.id, current_paid_hours=0,
        current_paid_value_rub=Decimal("0"), bonus_hours=0,
        confirmed_payment_required_rub=version.price_rub,
        resulting_paid_hours=version.duration_hours,
        resulting_paid_value_rub=version.price_rub, resulting_bonus_hours=0,
        rounding_loss_hours=Decimal("0"), rounding_loss_value_rub=Decimal("0"),
        currency="RUB", status="active", created_at=now,
        expires_at=now + QUOTE_LIFETIME,
    )
    session.add(quote)
    await session.flush()
    return quote, version
