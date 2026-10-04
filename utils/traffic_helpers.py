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
    if host_cycle != current_cycle:
        extra["host_traffic_cycle"] = current_cycle
        extra["traffic_cycle"] = current_cycle
        extra["host_monthly_traffic_bytes"] = 0
        extra["host_last_raw_bytes"] = raw_total
        return True

    last_raw = extra.get("host_last_raw_bytes")
    if last_raw is None:
        extra["host_last_raw_bytes"] = raw_total
        return True

    delta = (raw_total - last_raw) if raw_total >= last_raw else raw_total
    if delta > 0:
        extra["host_monthly_traffic_bytes"] = int(extra.get("host_monthly_traffic_bytes", 0)) + delta
        extra["host_last_raw_bytes"] = raw_total
        extra["traffic_cycle"] = current_cycle
        return True

    return False
