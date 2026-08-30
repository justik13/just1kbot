"""HTTP REST Client for interacting with the Origin server's xray-api daemon."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)


class XrayNodeClientError(RuntimeError):
    """Base exception for Xray Node API errors."""
    pass


class XrayNodeClient:
    """Client for node-level Xray API management (port 8444/tcp)."""

    def __init__(self, timeout: float = 10.0, max_retries: int = 2):
        self.timeout = timeout
        self.max_retries = max_retries

    def _get_headers(self, api_key: str) -> dict[str, str]:
        return {
            "X-API-Key": api_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    async def check_health(
        self, api_url: str, api_key: str
    ) -> tuple[bool, str | None, dict[str, Any] | None]:
        """
        Check health and retrieve the running xray_instance_epoch.
        Returns: (is_healthy, node_epoch, raw_response)
        """
        url = f"{api_url.rstrip('/')}/v1/health"
        headers = self._get_headers(api_key)
        client_timeout = aiohttp.ClientTimeout(total=self.timeout)

        try:
            async with aiohttp.ClientSession(timeout=client_timeout) as session:
                async with session.get(url, headers=headers, ssl=False) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        is_ok = data.get("status") == "ok" and data.get("xray_running", False)
                        node_epoch = data.get("node_epoch")
                        return is_ok, node_epoch, data
                    text = await resp.text()
                    logger.warning("Node health check failed with status %d: %s", resp.status, text)
                    return False, None, None
        except Exception as exc:
            logger.error("Failed to connect to xray-api health endpoint at %s: %s", url, exc)
            return False, None, None

    async def sync_client(
        self, api_url: str, api_key: str, client_uuid: str, is_active: bool
    ) -> tuple[bool, str | None]:
        """
        Idempotent synchronization of a client across inbounds.
        Returns: (success, error_message_if_any)
        """
        url = f"{api_url.rstrip('/')}/v1/clients/sync"
        headers = self._get_headers(api_key)
        payload = {
            "client_id": client_uuid,
            "desired_state": "active" if is_active else "disabled",
        }
        client_timeout = aiohttp.ClientTimeout(total=self.timeout)

        try:
            async with aiohttp.ClientSession(timeout=client_timeout) as session:
                async with session.post(url, headers=headers, json=payload, ssl=False) as resp:
                    if resp.status in (200, 201):
                        return True, None
                    text = await resp.text()
                    err = f"Sync failed with HTTP {resp.status}: {text}"
                    logger.error(err)
                    return False, err
        except Exception as exc:
            err = f"Network failure during sync with {url}: {exc}"
            logger.error(err)
            return False, err

    async def remove_client(
        self, api_url: str, api_key: str, client_uuid: str
    ) -> tuple[bool, str | None]:
        """Remove a client from all inbounds on the node."""
        url = f"{api_url.rstrip('/')}/v1/clients/{client_uuid}"
        headers = self._get_headers(api_key)
        client_timeout = aiohttp.ClientTimeout(total=self.timeout)

        try:
            async with aiohttp.ClientSession(timeout=client_timeout) as session:
                async with session.delete(url, headers=headers, ssl=False) as resp:
                    if resp.status in (200, 204):
                        return True, None
                    text = await resp.text()
                    err = f"Delete failed with HTTP {resp.status}: {text}"
                    logger.error(err)
                    return False, err
        except Exception as exc:
            err = f"Network failure during client deletion: {exc}"
            logger.error(err)
            return False, err

    async def get_traffic_snapshot(
        self, api_url: str, api_key: str
    ) -> tuple[str | None, dict[str, dict[str, int]] | None]:
        """
        Fetch normalized traffic snapshot across inbounds.
        Returns: (node_epoch, users_traffic_dict)
        where users_traffic_dict = { uuid: {"uplink": int, "downlink": int} }
        """
        url = f"{api_url.rstrip('/')}/v1/traffic/snapshot"
        headers = self._get_headers(api_key)
        client_timeout = aiohttp.ClientTimeout(total=self.timeout)

        try:
            async with aiohttp.ClientSession(timeout=client_timeout) as session:
                async with session.get(url, headers=headers, ssl=False) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        node_epoch = data.get("node_epoch")
                        users = data.get("users", {})
                        return node_epoch, users
                    text = await resp.text()
                    logger.error("Traffic snapshot fetch failed (%d): %s", resp.status, text)
                    return None, None
        except Exception as exc:
            logger.error("Network failure fetching traffic snapshot from %s: %s", url, exc)
            return None, None
