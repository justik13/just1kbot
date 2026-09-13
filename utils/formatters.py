"""General formatting helpers for traffic, datetime, breadcrumbs, and audit logs."""
from datetime import date, datetime, time, timezone

from utils.datetime_helpers import format_datetime_msk


def format_traffic(bytes_value: int) -> str:
    if bytes_value <= 0:
        return "0 B"

    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    size = bytes_value
    unit_index = 0

    while size >= 1024 and unit_index < len(units) - 1:
        size /= 1024
        unit_index += 1

    if unit_index == 0:
        return f"{int(size)} {units[unit_index]}"
    else:
        return f"{size:.1f} {units[unit_index]}"


def format_datetime(dt: datetime | None) -> str:
    return format_datetime_msk(dt, "%d.%m.%Y %H:%M")


def format_tg_time(
    dt: datetime | date | None,
    format_spec: str = "dt",
    fallback_format: str = "%d.%m.%Y %H:%M",
) -> str:
    """Render a datetime into a localized Telegram <tg-time> HTML tag.

    Telegram clients automatically convert the unix timestamp into the user's
    local time zone and language format.
    """
    if dt is None:
        return "—"

    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    elif isinstance(dt, date):
        dt = datetime.combine(dt, time.min, tzinfo=timezone.utc)
    else:
        return "—"

    unix_ts = int(dt.timestamp())
    fallback = format_datetime_msk(dt, fallback_format)
    if unix_ts <= 0:
        return fallback

    if format_spec:
        clean_spec = format_spec.replace(" ", "")
        return f'<tg-time unix="{unix_ts}" format="{clean_spec}">{fallback}</tg-time>'
    return f'<tg-time unix="{unix_ts}">{fallback}</tg-time>'


def get_country_display(country_flag: str | None, default_text: str = "🌐") -> str:
    """Return country flag string configured on the server, or default fallback."""
    if not country_flag:
        return default_text
    return country_flag.strip()
