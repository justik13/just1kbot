"""Lightweight helper functions for traffic accounting and node host metric accumulation."""

from __future__ import annotations

from datetime import datetime
from typing import NamedTuple


class ServerHostingUsage(NamedTuple):
    limit_gb: int | None
    reset_day: int
    current_cycle: str
    host_bytes: int
    host_gb: float
    pct: float


def get_server_hosting_reset_day(extra: dict | None) -> int:
    """Extracts clamped hosting billing reset day (1..31) from Server.extra_data."""
    raw = (extra or {}).get("hosting_reset_day", 1)
    if isinstance(raw, bool):
        return 1
    try:
        day = int(raw or 1)
    except (TypeError, ValueError):
        return 1
    return max(1, min(31, day))


def get_server_hosting_usage(
    extra: dict | None,
    current_time: datetime | None = None,
) -> ServerHostingUsage:
    """Computes current-cycle host traffic usage and hosting limit percentage from Server.extra_data."""
    from utils.datetime_helpers import get_hosting_billing_cycle, now_utc

    data = extra if isinstance(extra, dict) else {}
    now = current_time or now_utc()
    reset_day = get_server_hosting_reset_day(data)
    current_cycle = get_hosting_billing_cycle(now, reset_day)

    raw_limit = data.get("hosting_limit_gb")
    limit_gb: int | None = None
    if raw_limit is not None and not isinstance(raw_limit, bool):
        try:
            parsed_limit = int(raw_limit)
            if parsed_limit > 0:
                limit_gb = parsed_limit
        except (TypeError, ValueError):
            pass

    host_cycle = data.get("host_traffic_cycle")
    try:
        raw_bytes = int(data.get("host_monthly_traffic_bytes", 0) or 0)
    except (TypeError, ValueError):
        raw_bytes = 0
    host_bytes = max(0, raw_bytes) if host_cycle == current_cycle else 0
    host_gb = host_bytes / (1024 ** 3)
    pct = (host_bytes / (limit_gb * (1024 ** 3)) * 100.0) if limit_gb else 0.0

    return ServerHostingUsage(
        limit_gb=limit_gb,
        reset_day=reset_day,
        current_cycle=current_cycle,
        host_bytes=host_bytes,
        host_gb=host_gb,
        pct=pct,
    )


def accumulate_host_traffic_cycle(
    extra: dict,
    host_tx: int,
    host_rx: int,
    current_cycle: str,
) -> bool:
    """Accumulates cumulative host bytes into extra_data monthly metric with reboot protection.

    Returns True if extra was modified, False otherwise.
    """
    raw_total = max(0, int(host_tx or 0)) + max(0, int(host_rx or 0))
    if raw_total <= 0:
        return False

    host_cycle = extra.get("host_traffic_cycle")
    last_raw = extra.get("host_last_raw_bytes")

    if host_cycle != current_cycle:
        extra["host_traffic_cycle"] = current_cycle
        if last_raw is not None:
            delta = (raw_total - last_raw) if raw_total >= last_raw else raw_total
            extra["host_monthly_traffic_bytes"] = delta
        else:
            extra["host_monthly_traffic_bytes"] = 0
        extra["host_last_raw_bytes"] = raw_total
        return True

    if last_raw is None:
        extra["host_last_raw_bytes"] = raw_total
        return True

    delta = (raw_total - last_raw) if raw_total >= last_raw else raw_total
    if delta > 0:
        extra["host_monthly_traffic_bytes"] = int(extra.get("host_monthly_traffic_bytes", 0)) + delta
        extra["host_last_raw_bytes"] = raw_total
        return True

    return False


