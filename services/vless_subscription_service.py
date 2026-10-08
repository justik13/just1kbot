"""Service for standard VLESS subscriptions, link generation, and dual-node synchronization."""

from __future__ import annotations

import asyncio
import logging
import os
import urllib.parse

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from config.enums import ServerHealthState
from database.models import Server, User, VlessSubscription, VPNProfile
from database.repositories import vless_subscription_repo
from services.device_service import RESERVING_STATUSES
from services.xray_node_client import XrayNodeClient

logger = logging.getLogger(__name__)


class VlessSubscriptionService:
    """Business logic for standard VLESS subscriptions."""

    @staticmethod
    def generate_vless_links(
        subscription: VlessSubscription,
        servers: list[Server],
    ) -> list[str]:
        """Generate VLESS TLS xtls-rprx-vision links for given servers."""
        links: list[str] = []
        for srv in servers:
            if not srv.is_active or srv.health_state != ServerHealthState.ONLINE:
                continue

            extra = srv.extra_data if isinstance(srv.extra_data, dict) else {}
            domain = extra.get("domain")
            if not domain and srv.api_url:
                parsed = urllib.parse.urlsplit(srv.api_url)
                domain = parsed.hostname

            if not domain:
                continue

            port = int(extra.get("vless_port", 443))
            flag = srv.country_flag or "🌐"
            label = f"{flag} {srv.name}".strip()
            encoded_label = urllib.parse.quote(label)

            link = (
                f"vless://{subscription.uuid}@{domain}:{port}"
                f"?encryption=none&security=tls&sni={domain}&alpn=h2%2Chttp%2F1.1"
                f"&type=tcp&flow=xtls-rprx-vision#{encoded_label}"
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
                domain = None
        if not domain:
            domain = os.getenv("DOMAIN") or os.getenv("BOT_DOMAIN") or "just1k.pro"
        clean_domain = domain.strip().replace("https://", "").replace("http://", "").rstrip("/")
        return f"https://{clean_domain}/sub/vless/{token}"

    @classmethod
    def ensure_synced_background(cls, user_id: int, is_active: bool = True) -> asyncio.Task:
        """Fire and forget node synchronization in a fresh session scope."""
        async def _run() -> None:
            from database.connection import session_scope
            try:
                async with session_scope() as session:
                    await cls.sync_user_to_dual_nodes(session, user_id, is_active=is_active)
            except Exception as e:
                logger.warning("Background sync of VLESS user %s failed: %s", user_id, e)

        return asyncio.create_task(_run())

    @staticmethod
    async def get_combined_quota(
        session: AsyncSession,
        user: User,
    ) -> tuple[int, int, int]:
        """Returns (total_active, awg_active_count, vless_hwid_count)."""
        awg_count = (
            await session.execute(
                select(func.count(VPNProfile.id)).where(
                    VPNProfile.user_id == user.id,
                    VPNProfile.provisioning_status.in_(RESERVING_STATUSES),
                )
            )
        ).scalar_one()

        vless_count = await vless_subscription_repo.get_active_hwid_count(session, user.id)
        total = awg_count + vless_count
        return total, awg_count, vless_count

    @staticmethod
    async def get_eligible_vless_servers(
        session: AsyncSession,
    ) -> list[Server]:
        """Fetch all online servers capable of serving VLESS TLS."""
        stmt = (
            select(Server)
            .where(
                Server.is_active.is_(True),
                Server.health_state == ServerHealthState.ONLINE,
            )
            .order_by(Server.id.asc())
        )
        servers = (await session.execute(stmt)).scalars().all()
        eligible: list[Server] = []
        for srv in servers:
            caps = srv.capabilities or []
            proto = (srv.protocol or "").lower()
            if proto in ("dual", "relay", "vless") or "dual" in caps or "xray_vless" in caps:
                eligible.append(srv)
        return eligible

    @staticmethod
    async def sync_user_to_dual_nodes(
        session: AsyncSession,
        user_id: int,
        is_active: bool = True,
    ) -> dict[int, bool]:
        """Provision or disable user UUID on all eligible dual nodes via their Xray API."""
        sub = await vless_subscription_repo.get_subscription_by_user_id(session, user_id)
        if sub is None:
            return {}

        servers = await VlessSubscriptionService.get_eligible_vless_servers(session)
        results: dict[int, bool] = {}

        for srv in servers:
            extra = srv.extra_data if isinstance(srv.extra_data, dict) else {}
            api_url = extra.get("xray_api_url")
            api_key = extra.get("xray_api_key")

            # Fallback to resolving from srv.api_url and port 8444
            if not api_url and srv.api_url:
                parsed = urllib.parse.urlsplit(srv.api_url)
                if parsed.hostname:
                    api_url = f"https://{parsed.hostname}:8444"
            if not api_key:
                api_key = srv.api_key

            if not api_url or not api_key:
                continue

            try:
                async with XrayNodeClient(timeout=5.0) as client:
                    resp = await client.sync_client(
                        api_url=api_url,
                        api_key=api_key,
                        client_uuid=sub.uuid,
                        is_active=is_active,
                    )
                    results[srv.id] = (resp.result in ("applied", "already_newer"))
            except Exception as exc:
                logger.warning("Failed to sync VLESS user %s to server %s: %s", sub.uuid, srv.id, exc)
                results[srv.id] = False

        return results
