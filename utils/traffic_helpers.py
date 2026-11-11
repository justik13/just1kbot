"""Lightweight helper functions for traffic accounting and node host metric accumulation."""

from __future__ import annotations


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


def get_user_monthly_standard_traffic(user: object | None, current_cycle: str | None = None) -> int:
    """Returns standard subscription VLESS traffic for the active cycle."""
    if not user:
        return 0
    if current_cycle is None:
        from utils.datetime_helpers import now_utc

        current_cycle = now_utc().strftime("%Y-%m")
    user_cycle = getattr(user, "traffic_cycle", None)
    if user_cycle != current_cycle:
        return 0
    vless = getattr(user, "monthly_vless_bytes", 0) or 0
    return max(0, int(vless))


