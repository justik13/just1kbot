"""General formatting helpers for traffic, datetime, breadcrumbs, and audit logs."""
from datetime import datetime

from utils.datetime_helpers import format_datetime_msk


def format_traffic(bytes_value: int) -> str:
    if bytes_value == 0:
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



def get_country_display(country_flag: str | None, default_text: str = "🌐") -> str:
    """Return country flag string configured on the server, or default fallback."""
    if not country_flag:
        return default_text
    return country_flag.strip()
