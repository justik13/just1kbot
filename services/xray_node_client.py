"""HTTP REST Client for interacting with the Origin server's xray-api daemon."""

from __future__ import annotations

import asyncio
import enum
import logging
import os
import re
import ssl
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)


def _sanitize_url(url: str) -> str:
    """Mask UUIDs and sensitive endpoints in URL for safe logging."""
    return re.sub(r"/v1/clients/[a-fA-F0-9-]+", "/v1/clients/[MASKED]", url)


class SyncResult(str, enum.Enum):
    APPLIED = "applied"
    ALREADY_NEWER = "already_newer"
    FENCED = "fenced"
    FAILED = "failed"


class XrayNodeClientError(RuntimeError):
    """Base exception for Xray Node API errors."""
    pass


class XrayNodeClient:
    """Client for node-level Xray API management (port 8444/tcp)."""

    def __init__(self, timeout: float = 10.0, max_retries: int = 2, ca_file: str | None = None):
        self.timeout = timeout
        self.max_retries = max_retries
        self.ca_file = ca_file or os.getenv("XRAY_NODE_CA_FILE")
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> XrayNodeClient:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()

    async def close(self) -> None:
        """Close internal aiohttp ClientSession and connector pool."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    def _get_headers(self, api_key: str) -> dict[str, str]:
        return {
            "X-API-Key": api_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _ssl_context(self) -> ssl.SSLContext:
        """Create a verified TLS context; custom CA is supported for node certificates."""
        return ssl.create_default_context(cafile=self.ca_file)

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(
                limit=50,
                ttl_dns_cache=300,
                enable_cleanup_closed=True,
                ssl=self._ssl_context(),
            )
            client_timeout = aiohttp.ClientTimeout(total=self.timeout)
            self._session = aiohttp.ClientSession(
                timeout=client_timeout,
                connector=connector,
            )
        return self._session

    async def _make_request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        json_data: dict[str, Any] | None = None,
    ) -> tuple[int, Any, str | None]:
        safe_url = _sanitize_url(url)
        session = await self._get_session()

        for attempt in range(self.max_retries + 1):
            try:
                async with session.request(
                    method, url, headers=headers, json=json_data
                ) as resp:
                    status_code = resp.status
                    if status_code in (200, 201):
                        try:
                            data = await resp.json()
                            return status_code, data, None
                        except Exception:
                            text = await resp.text()
                            return status_code, text, None
                    if status_code in (204,):
                        return status_code, None, None

                    text = await resp.text()
                    if status_code in (502, 503, 504) and attempt < self.max_retries:
                        logger.warning(
                            "%s %s failed with status %d (attempt %d/%d), retrying...",
                            method, safe_url, status_code, attempt + 1, self.max_retries + 1
                        )
                        await asyncio.sleep(0.5 * (2**attempt))
                        continue
                    return status_code, None, f"HTTP {status_code}: {text}"
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                if attempt < self.max_retries:
                    logger.warning(
                        "%s %s failed with %s (attempt %d/%d), retrying...",
                        method, safe_url, exc, attempt + 1, self.max_retries + 1
                    )
                    await asyncio.sleep(0.5 * (2**attempt))
                    continue
                return 0, None, f"Network failure: {exc}"
            except Exception as exc:
                return 0, None, f"Unexpected failure: {exc}"

        return 0, None, "Max retries exceeded"

    async def check_health(
        self, api_url: str, api_key: str
    ) -> tuple[bool, str | None, dict[str, Any] | None]:
        """Check node health fail-closed: must have status=ok, xray_running=True, grpc_ok=True."""
        url = f"{api_url.rstrip('/')}/v1/health"
        headers = self._get_headers(api_key)
        status_code, data, err = await self._make_request("GET", url, headers)

        if status_code == 200 and isinstance(data, dict):
            is_ok = (
                data.get("status") == "ok"
                and data.get("xray_running", False) is True
                and data.get("grpc_ok", False) is True
            )
            node_epoch = data.get("node_epoch")
            return is_ok, node_epoch, data

        logger.warning("Node health check failed for %s: %s", _sanitize_url(url), err)
        return False, None, None

    async def sync_client(
        self,
        api_url: str,
        api_key: str,
        client_uuid: str,
        is_active: bool,
        version: int | None = None,
    ) -> tuple[SyncResult, str | None]:
        """Idempotently synchronize a client across both Origin inbounds with monotonic version fencing."""
        url = f"{api_url.rstrip('/')}/v1/clients/sync"
        headers = self._get_headers(api_key)
        payload: dict[str, Any] = {
            "client_id": client_uuid,
            "desired_state": "active" if is_active else "disabled",
        }
        if version is not None:
            payload["version"] = version
        status_code, data, err = await self._make_request("POST", url, headers, json_data=payload)
        if status_code in (200, 201) and isinstance(data, dict):
            res_str = data.get("result", "applied")
            if res_str == "already_newer":
                return SyncResult.ALREADY_NEWER, None
            if res_str == "fenced":
                return SyncResult.FENCED, "Request was fenced by node version"
            return SyncResult.APPLIED, None
        if status_code in (409, 412):
            return SyncResult.FENCED, err or "Version fenced"
        return SyncResult.FAILED, err or f"Sync failed with HTTP {status_code}"

    async def remove_client(
        self,
        api_url: str,
        api_key: str,
        client_uuid: str,
        version: int | None = None,
    ) -> tuple[SyncResult, str | None]:
        """Remove a client from all inbounds on the node with version fencing."""
        url = f"{api_url.rstrip('/')}/v1/clients/{client_uuid}"
        if version is not None:
            url = f"{url}?version={version}"
        headers = self._get_headers(api_key)
        status_code, data, err = await self._make_request("DELETE", url, headers)
        if status_code in (200, 204):
            if isinstance(data, dict):
                res_str = data.get("result", "applied")
                if res_str == "already_newer":
                    return SyncResult.ALREADY_NEWER, None
                if res_str == "fenced":
                    return SyncResult.FENCED, "Request was fenced by node version"
            return SyncResult.APPLIED, None
        if status_code in (409, 412):
            return SyncResult.FENCED, err or "Version fenced"
        return SyncResult.FAILED, err or f"Delete failed with HTTP {status_code}"

    async def get_traffic_snapshot(
        self, api_url: str, api_key: str
    ) -> tuple[str | None, str | None, int | None, dict[str, dict[str, int]] | None]:
        """Fetch normalized traffic snapshot across all configured inbounds."""
        url = f"{api_url.rstrip('/')}/v1/traffic/snapshot"
        headers = self._get_headers(api_key)
        status_code, data, err = await self._make_request("GET", url, headers)
        if status_code == 200 and isinstance(data, dict):
            node_epoch = data.get("node_epoch")
            node_boot_id = data.get("boot_id")
            node_starttime = data.get("starttime")
            users = data.get("users", {})
            return node_epoch, node_boot_id, node_starttime, users
        logger.error("Traffic snapshot fetch failed for %s: %s", _sanitize_url(url), err)
        return None, None, None, None
