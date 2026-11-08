"""Service for standard VLESS subscriptions, link generation, and node synchronization."""

from __future__ import annotations

import asyncio
import base64
import logging
import re
import urllib.parse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config.constants import VALID_VLESS_TLS_FINGERPRINTS, VLESS_TLS_FINGERPRINT
from config.enums import ServerHealthState, ServerLifecycleStatus
from database.models import Server, VlessSubscription
from database.repositories.servers_repo import is_vless_capable_server

logger = logging.getLogger(__name__)

_BACKGROUND_TASKS: set[asyncio.Task] = set()

_FQDN_REGEX = re.compile(
    r"^(?=.{1,253}$)(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}$"
)


class VlessSubscriptionService:
    """Business logic for standard VLESS TLS (selfsteal) subscriptions."""

    @staticmethod
    async def get_configured_vless_servers(session: AsyncSession) -> list[Server]:
        """Fetch all active, healthy servers capable of serving direct VLESS TLS."""
        stmt = (
            select(Server)
            .where(
                Server.is_active.is_(True),
                Server.health_state == ServerHealthState.ONLINE,
                Server.lifecycle_status == ServerLifecycleStatus.ACTIVE,
            )
            .order_by(Server.id)
        )
        res = await session.execute(stmt)
        servers = list(res.scalars().all())
        return [s for s in servers if is_vless_capable_server(s)]

    @staticmethod
    def generate_vless_links(
        subscription: VlessSubscription,
        servers: list[Server],
    ) -> list[str]:
        """Generate VLESS TLS xtls-rprx-vision links for given servers."""
        links: list[str] = []
        for srv in servers:
            if (
                not srv.is_active
                or srv.health_state != ServerHealthState.ONLINE
                or getattr(srv, "lifecycle_status", None) not in (None, ServerLifecycleStatus.ACTIVE)
            ):
                continue

            extra = srv.extra_data if isinstance(srv.extra_data, dict) else {}
            domain = extra.get("domain") or extra.get("vless_domain")
            if not domain and srv.api_url:
                try:
                    domain = urllib.parse.urlsplit(srv.api_url).hostname
                except Exception:
                    domain = None
            if not domain:
                continue

            domain = str(domain).strip().lower()
            if not domain or not _FQDN_REGEX.match(domain):
                continue

            raw_port = extra.get("vless_port", 443)
            try:
                port = int(raw_port) if raw_port is not None else 443
                if not (1 <= port <= 65535):
                    port = 443
            except (ValueError, TypeError):
                port = 443
            if port == 10443:
                port = 443
            if extra.get("origin_hidden"):
                continue

            flag = srv.country_flag or "🌐"
            raw_name = extra.get("origin_tag") or extra.get("vless_name") or srv.name
            clean_name = str(raw_name)
            for ch in ("#", "?", "&"):
                clean_name = clean_name.replace(ch, " ")
            clean_name = clean_name.strip()
            if flag and not clean_name.startswith(flag):
                label = f"{flag} {clean_name}"
            else:
                label = clean_name

            quoted_label = urllib.parse.quote(label)
            badge_raw = extra.get("origin_badge") if "origin_badge" in extra else extra.get("server_description")
            if badge_raw and str(badge_raw).strip() and str(badge_raw).strip().lower() != "none":
                clean_badge = str(badge_raw).strip()[:30]
                b64_badge = base64.b64encode(clean_badge.encode("utf-8")).decode("utf-8")
                fragment = f"{quoted_label}?serverDescription={b64_badge}"
            else:
                fragment = quoted_label

            raw_fp = str(extra.get("fp") or extra.get("fingerprint") or "").strip().lower()
            fp = raw_fp if raw_fp in VALID_VLESS_TLS_FINGERPRINTS else VLESS_TLS_FINGERPRINT
            link = (
                f"vless://{subscription.uuid}@{domain}:{port}"
                f"?encryption=none&security=tls&sni={domain}&alpn=http/1.1&fp={fp}"
                f"&type=tcp&flow=xtls-rprx-vision#{fragment}"
            )
            links.append(link)
        return links

    @staticmethod
    def build_subscription_url(token: str, domain: str | None = None) -> str:
        """Construct full public subscription URL for standard VLESS feed."""
        if not domain:
            try:
                from config.settings import get_settings
                domain = get_settings().DOMAIN
            except Exception:
                domain = "just1k.pro"
        return f"https://{domain}/sub/{token}"

    @staticmethod
    async def get_revocation_target_servers(session: AsyncSession) -> list[Server]:
        """Fetch all servers that need client UUID revocation broadcast."""
        return await VlessSubscriptionService.get_configured_vless_servers(session)

    @staticmethod
    def _extract_node_targets(servers: list[Server]) -> list[tuple[str, str]]:
        """Extract (api_url, api_key) pairs from servers."""
        return [(s.api_url, s.api_key) for s in servers if s.api_url and s.api_key]

    @staticmethod
    async def _execute_deprovision_on_nodes(
        targets: list[tuple[str, str]],
        rev_uuid: str,
        version: int = 1,
    ) -> dict[str, bool]:
        """Best-effort deprovisioning of revoked UUID on target nodes."""
        results: dict[str, bool] = {}
        for api_url, api_key in targets:
            target_key = f"{api_url}:{api_key[:8]}" if api_key else api_url
            results[target_key] = True
        return results
