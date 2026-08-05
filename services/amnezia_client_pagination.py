"""Reliable paginated client listing for the Amnezia API.

The repository uses aiohttp in ``AmneziaClient``. This helper keeps pagination
retries on top of that existing transport and deliberately avoids introducing
another HTTP dependency just for retry handling.
"""

from __future__ import annotations

import asyncio
from typing import Any

from services.amnezia_client import AmneziaClient, RequestSemantics


async def get_all_clients_with_retry(
    client: AmneziaClient,
    *,
    page_size: int = 100,
    max_pages: int = 100,
    max_attempts_per_page: int = 3,
) -> list[Any] | None:
    """Fetch every Amnezia client page with bounded exponential retries."""
    all_clients = []

    for page_number in range(max_pages):
        result = None

        for attempt in range(max_attempts_per_page):
            response = await client._request_result(
                "GET",
                "/clients",
                semantics=RequestSemantics.READ,
                params={
                    "skip": page_number * page_size,
                    "limit": page_size,
                },
            )

            if response.ok:
                result = response.value
                break

            if not response.retryable or attempt + 1 >= max_attempts_per_page:
                return None

            await asyncio.sleep(2**attempt)

        if result is None:
            return None

        if isinstance(result, list):
            raw_items = result
        elif isinstance(result, dict):
            raw_items = (
                result.get("items")
                or result.get("clients")
                or result.get("data")
                or []
            )
            if isinstance(raw_items, dict):
                raw_items = [raw_items]
        else:
            return None

        if not isinstance(raw_items, list):
            return None

        if not raw_items:
            break

        parsed = client._parse_clients_page(raw_items)
        if raw_items and not parsed:
            return None

        all_clients.extend(parsed)

        if len(raw_items) < page_size:
            break
    else:
        return None

    return all_clients


def install_amnezia_client_pagination_patch() -> None:
    """Replace the dependency-heavy pagination implementation at import time."""
    AmneziaClient.get_all_clients = get_all_clients_with_retry
