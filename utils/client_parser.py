"""Universal parser for subscription client identification from User-Agent."""

from __future__ import annotations

import re


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
