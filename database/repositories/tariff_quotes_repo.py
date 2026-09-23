"""Persistence boundary for checkout user locks and tariff versions."""

from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from config.constants import WHITE_INTERNET_BASE_TRAFFIC_BYTES
from config.enums import ServiceType
from database.models import Tariff, TariffVersion, User


class CheckoutQuoteConflictError(RuntimeError):
    pass


async def lock_checkout_user(session: AsyncSession, user_id: int) -> User | None:
    """The sole per-user checkout lock; callers derive state only afterwards."""
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": -user_id})
    return await session.scalar(
        select(User).where(User.id == user_id).with_for_update()
    )


async def get_or_create_current_version(
    session: AsyncSession, tariff: Tariff
) -> TariffVersion:
    """Serializes version allocation and prevents two distinct snapshots for one edit."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:key)"), {"key": tariff.id}
    )
    base_quota = (
        WHITE_INTERNET_BASE_TRAFFIC_BYTES
        if tariff.service_type == ServiceType.WHITE_INTERNET
        else None
    )
    version = await session.scalar(
        select(TariffVersion)
        .where(
            TariffVersion.tariff_id == tariff.id,
            TariffVersion.name_snapshot == tariff.name,
            TariffVersion.duration_hours == tariff.duration_days * 24,
            TariffVersion.device_limit == tariff.device_limit,
            TariffVersion.price_rub == Decimal(tariff.price_rub),
            TariffVersion.currency == "RUB",
            TariffVersion.service_type == tariff.service_type,
            TariffVersion.base_quota_bytes == base_quota,
        )
        .order_by(TariffVersion.version_number.desc())
        .limit(1)
    )
    if version:
        return version
    number = (
        await session.scalar(
            select(TariffVersion.version_number)
            .where(TariffVersion.tariff_id == tariff.id)
            .order_by(TariffVersion.version_number.desc())
            .limit(1)
        )
        or 0
    ) + 1
    version = TariffVersion(
        tariff_id=tariff.id,
        version_number=number,
        name_snapshot=tariff.name,
        duration_hours=tariff.duration_days * 24,
        device_limit=tariff.device_limit,
        price_rub=Decimal(tariff.price_rub),
        currency="RUB",
        service_type=tariff.service_type,
        base_quota_bytes=base_quota,
    )
    session.add(version)
    await session.flush()
    return version
