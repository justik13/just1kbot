from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

MSK_TZ = ZoneInfo("Europe/Moscow")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_msk() -> datetime:
    return datetime.now(MSK_TZ)


def to_msk(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(MSK_TZ)


def format_datetime_msk(
    dt: datetime | None,
    format_str: str = "%d.%m.%Y %H:%M",
) -> str:
    if dt is None:
        return "—"

    msk_dt = to_msk(dt)
    return msk_dt.strftime(format_str)


def is_permanent_subscription(dt: datetime | None) -> bool:
    if dt is None:
        return False
    return dt.year >= 2100 or (dt.replace(tzinfo=timezone.utc) - now_utc()).days >= 36500


def is_expired(dt: datetime | None) -> bool:
    if dt is None:
        return True

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt < now_utc()


def is_vpn_access_expired(dt: datetime | None, grace_hours: int = 4) -> bool:
    if dt is None:
        return True

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return (dt + timedelta(hours=grace_hours)) < now_utc()


def calculate_extension_end(
    current_end: datetime | None,
    days: int,
    now: datetime | None = None,
) -> datetime:
    """Calculate the new expiration datetime given the current expiration and added days."""
    from config.constants import PERMANENT_END_DATE, PERMANENT_SUBSCRIPTION_DAYS

    if days >= PERMANENT_SUBSCRIPTION_DAYS:
        return PERMANENT_END_DATE

    current_time = now or now_utc()
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)

    if current_end is not None and current_end.tzinfo is None:
        current_end = current_end.replace(tzinfo=timezone.utc)

    base_time = current_end if (current_end and current_end > current_time) else current_time
    return base_time + timedelta(days=days)

