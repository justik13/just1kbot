from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    MSK_TZ = ZoneInfo("Europe/Moscow")
except (ZoneInfoNotFoundError, KeyError):
    MSK_TZ = timezone(timedelta(hours=3), name="MSK")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_msk() -> datetime:
    return datetime.now(MSK_TZ)


def to_msk(dt: datetime | date | None) -> datetime | None:
    if dt is None:
        return None
    if isinstance(dt, date) and not isinstance(dt, datetime):
        dt = datetime.combine(dt, time.min, tzinfo=timezone.utc)
    elif not isinstance(dt, datetime):
        return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(MSK_TZ)


def format_datetime_msk(
    dt: datetime | date | None,
    format_str: str = "%d.%m.%Y %H:%M",
) -> str:
    if dt is None:
        return "—"

    msk_dt = to_msk(dt)
    if msk_dt is None:
        return "—"
    return msk_dt.strftime(format_str)


def is_permanent_subscription(dt: datetime | date | None) -> bool:
    if dt is None or not hasattr(dt, "year"):
        return False
    if dt.year >= 2100:
        return True
    if isinstance(dt, date) and not isinstance(dt, datetime):
        dt = datetime.combine(dt, time.min, tzinfo=timezone.utc)
    elif dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (dt - now_utc()).days >= 36500


def is_expired(dt: datetime | date | None) -> bool:
    if dt is None:
        return True
    if isinstance(dt, date) and not isinstance(dt, datetime):
        dt = datetime.combine(dt, time.min, tzinfo=timezone.utc)
    elif not isinstance(dt, datetime):
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

