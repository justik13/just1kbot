"""Universal parser for subscription client identification from User-Agent."""

from __future__ import annotations

import re


from typing import Any, Mapping


def parse_client_from_user_agent(ua: str | None) -> str | None:
    """Parse client name and version from User-Agent (INCY, Happ, or generic HWID client)."""
    if not ua or not isinstance(ua, str):
        return None

    match = re.search(r"\b(incy|happ)[/\s][vV]?([0-9]+(?:\.[0-9A-Za-z_.-]+)*)", ua, re.IGNORECASE)
    if match:
        client_name = "INCY" if match.group(1).lower() == "incy" else "Happ"
        return f"{client_name} {match.group(2)}"

    clean_ua = re.sub(r"\([^()]*\)", " ", ua)
    match = re.search(r"\b([A-Za-z0-9_.-]+)/[vV]?([0-9]+(?:\.[0-9A-Za-z_.-]+)*)", clean_ua)
    if match:
        return f"{match.group(1)} {match.group(2)}"

    return None


def extract_device_metadata(headers: Mapping[str, Any] | Any | None = None) -> dict[str, str]:
    """Extract clean device metadata (client, os, model, ua) from request headers.

    Accepts an aiohttp web.Request, a headers mapping, or None.
    """
    if headers is None:
        return {}
    if hasattr(headers, "headers"):
        headers = headers.headers
    if not hasattr(headers, "get"):
        return {}

    raw_os = (
        headers.get("X-Device-Os")
        or headers.get("X-Device-OS")
        or headers.get("X-OS")
        or headers.get("x-device-os")
        or headers.get("x-os")
        or ""
    )
    raw_ver = (
        headers.get("X-Ver-Os")
        or headers.get("X-Ver-OS")
        or headers.get("x-ver-os")
        or ""
    )
    raw_model = (
        headers.get("X-Device-Model")
        or headers.get("X-Device-MODEL")
        or headers.get("X-Model")
        or headers.get("x-device-model")
        or headers.get("x-model")
        or ""
    )
    ua = headers.get("User-Agent") or headers.get("user-agent") or ""

    clean_os = re.sub(r"[^\w\s\.\-]", "", str(raw_os)).strip()[:32]
    clean_ver = re.sub(r"[^\w\s\.\-]", "", str(raw_ver)).strip()[:16]
    clean_model = re.sub(r"[^\w\s\.\-,]", "", str(raw_model)).strip()[:48]

    meta: dict[str, str] = {}
    client = parse_client_from_user_agent(str(ua))
    if client:
        meta["client"] = client[:64]

    clean_ua = re.sub(r"[\r\n]", " ", str(ua)).strip()[:64]
    if clean_os and clean_ver:
        meta["os"] = f"{clean_os} {clean_ver}"[:32]
    elif clean_os:
        meta["os"] = clean_os
    elif clean_ver:
        meta["os"] = clean_ver[:32]

    if clean_model:
        meta["model"] = clean_model
    if clean_ua:
        meta["ua"] = clean_ua
    return meta
