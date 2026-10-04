"""Lightweight helper functions for traffic accounting and node host metric accumulation."""

from __future__ import annotations

import re

_SLOT_NUM_RE = re.compile(r"#(\d+)$")


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



def get_device_slot_key(device_name: str | None) -> str:
    """Extract canonical slot key ('slot_1', 'slot_2', etc.) from device name."""
    if not device_name:
        return "slot_1"
    match = _SLOT_NUM_RE.search(device_name)
    if match:
        return f"slot_{match.group(1)}"
    return f"slot_{device_name.strip().lower()}"


def get_archived_traffic_for_device(archived: dict | None, device_name: str | None) -> int:
    """Safely retrieves accumulated archived traffic for a device slot.

    Supports canonical slot key ('slot_1'), exact name, and case-insensitive match.
    """
    if not archived or not device_name:
        return 0
    slot_key = get_device_slot_key(device_name)
    if slot_key in archived:
        return int(archived[slot_key] or 0)
    if device_name in archived:
        return int(archived[device_name] or 0)
    for k, v in archived.items():
        if k.lower() == device_name.lower():
            return int(v or 0)
    return 0


def record_device_traffic_archive(archived: dict, device_name: str | None, cur_bytes: int) -> dict:
    """Accumulates cur_bytes into archived dictionary using canonical slot key.

    Also migrates any legacy entry for device_name into the canonical slot key.
    """
    if cur_bytes <= 0 and not device_name:
        return archived
    slot_key = get_device_slot_key(device_name)
    legacy_bytes = 0
    if device_name and device_name in archived and device_name != slot_key:
        legacy_bytes = int(archived.pop(device_name, 0) or 0)

    archived[slot_key] = int(archived.get(slot_key, 0)) + legacy_bytes + max(0, cur_bytes)
    return archived

