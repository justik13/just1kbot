"""Service for standard VLESS subscriptions, link generation, and dual-node synchronization."""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import urllib.parse

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from config.enums import ServerHealthState, ServerLifecycleStatus
from database.models import Server, User, VlessSubscription, VPNProfile
from database.repositories import vless_subscription_repo
from services.device_service import RESERVING_STATUSES
from services.xray_node_client import XrayNodeClient
from utils.datetime_helpers import now_utc

logger = logging.getLogger(__name__)

_BACKGROUND_TASKS: set[asyncio.Task] = set()


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
            if (
                not srv.is_active
                or srv.health_state != ServerHealthState.ONLINE
                or getattr(srv, "lifecycle_status", None) not in (None, ServerLifecycleStatus.ACTIVE)
            ):
                continue

            extra = srv.extra_data if isinstance(srv.extra_data, dict) else {}
            domain = extra.get("domain")
            if not domain and srv.api_url:
                parsed = urllib.parse.urlsplit(srv.api_url)
                domain = parsed.hostname

            if not domain:
                continue

            domain = str(domain).strip().lower()
            if not domain or " " in domain or "/" in domain:
                continue

            raw_port = extra.get("vless_port", 443)
            try:
                port = int(raw_port) if raw_port is not None else 443
                if not (1 <= port <= 65535):
                    port = 443
            except (ValueError, TypeError):
                port = 443
            flag = srv.country_flag or "🌐"
            label = f"{flag} {srv.name}".strip()
            # INCY renders vector flag when emoji is the first character after #
            # and displays serverDescription badge via ?serverDescription=base64(UTF-8)
            badge_desc = extra.get("server_description") or texts.VLESS_DEFAULT_SERVER_DESCRIPTION
            b64_badge = base64.b64encode(badge_desc.encode("utf-8")).decode("utf-8")
            fragment = f"{label}?serverDescription={b64_badge}"

            link = (
                f"vless://{subscription.uuid}@{domain}:{port}"
                f"?encryption=none&security=tls&sni={domain}&alpn=h2%2Chttp%2F1.1"
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
                domain = None
        if not domain:
            domain = os.getenv("DOMAIN") or os.getenv("BOT_DOMAIN") or "just1k.pro"
        clean_domain = domain.strip().replace("https://", "").replace("http://", "").rstrip("/")
        return f"https://{clean_domain}/sub/vless/{token}"

    @classmethod
    def ensure_synced_background(
        cls, user_id: int, is_active: bool = True, *, session: AsyncSession | None = None
    ) -> asyncio.Task | None:
        """Synchronize user on nodes with post-commit dispatch if session is active."""
        async def _run() -> None:
            from database.connection import session_scope
            try:
                sub_uuid = None
                sub_id = None
                desired_active = False
                targets = []
                async with session_scope() as scoped_session:
                    sub = await vless_subscription_repo.get_subscription_by_user_id(scoped_session, user_id)
                    if sub is not None:
                        sub_uuid = sub.uuid
                        sub_id = sub.id
                        from services.subscription import SubscriptionService
                        user = await scoped_session.get(User, user_id)
                        desired_active = bool(
                            sub.is_active
                            and user is not None
                            and not getattr(user, "is_banned", False)
                            and not getattr(user, "financial_hold", False)
                            and not getattr(user, "is_deleted", False)
                            and SubscriptionService.check_vpn_access(user)
                        )
                        if is_active is False:
                            desired_active = False
                        servers = await cls.get_eligible_vless_servers(scoped_session)
                        targets = cls._extract_node_targets(servers)

                if not targets or not sub_uuid:
                    return

                results = await cls._execute_sync_to_nodes(
                    targets, sub_uuid, desired_active, user_id=user_id, sub_id=sub_id
                )

                if any(results.values()):
                    async with session_scope() as scoped_session:
                        current_sub = await vless_subscription_repo.get_subscription_by_user_id(scoped_session, user_id)
                        if current_sub:
                            current_sub.last_synced_at = now_utc()
            except Exception as e:
                logger.warning("Background sync of VLESS user %s failed: %s", user_id, e)

        if session is not None and hasattr(session, "info") and isinstance(session.info, dict):
            from database.connection import queue_post_commit_task
            queue_post_commit_task(session, _run)
            return None

        task = asyncio.create_task(_run())
        _BACKGROUND_TASKS.add(task)
        task.add_done_callback(_BACKGROUND_TASKS.discard)
        return task

    @classmethod
    def deprovision_background(
        cls, old_uuid: str, *, session: AsyncSession | None = None
    ) -> asyncio.Task | None:
        """Deprovision user UUID from nodes with post-commit dispatch if session is active."""
        async def _run() -> None:
            from database.connection import session_scope
            try:
                targets = []
                async with session_scope() as scoped_session:
                    servers = await cls.get_eligible_vless_servers(scoped_session)
                    targets = cls._extract_node_targets(servers)

                if targets:
                    await cls._execute_deprovision_on_nodes(targets, old_uuid)
            except Exception as e:
                masked_uuid = f"{old_uuid[:8]}***" if old_uuid else "unknown"
                logger.warning("Background deprovision of VLESS UUID %s failed: %s", masked_uuid, e)

        if session is not None and hasattr(session, "info") and isinstance(session.info, dict):
            from database.connection import queue_post_commit_task
            queue_post_commit_task(session, _run)
            return None

        task = asyncio.create_task(_run())
        _BACKGROUND_TASKS.add(task)
        task.add_done_callback(_BACKGROUND_TASKS.discard)
        return task

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
                Server.lifecycle_status == ServerLifecycleStatus.ACTIVE,
            )
            .order_by(Server.id.asc())
        )
        servers = (await session.execute(stmt)).scalars().all()
        eligible: list[Server] = []
        for srv in servers:
            caps = srv.capabilities or []
            proto = (srv.protocol or "").lower()
            if "vless" in caps or "xray_vless" in caps or "dual" in caps or proto in ("dual", "vless"):
                eligible.append(srv)
        return eligible

    @staticmethod
    def _extract_node_targets(servers: list[Server]) -> list[tuple[int, str, str]]:
        targets: list[tuple[int, str, str]] = []
        for srv in servers:
            extra = srv.extra_data if isinstance(srv.extra_data, dict) else {}
            api_url = extra.get("xray_api_url")
            api_key = srv.api_key

            # Fallback to resolving from srv.api_url (which already has host:8443 on exit nodes)
            if not api_url and srv.api_url:
                parsed = urllib.parse.urlsplit(srv.api_url)
                if parsed.hostname:
                    port = parsed.port or 8443
                    api_url = f"{parsed.scheme or 'https'}://{parsed.hostname}:{port}"

            if api_url and api_key:
                targets.append((srv.id, api_url, api_key))
        return targets

    @staticmethod
    async def _execute_sync_to_nodes(
        targets: list[tuple[int, str, str]],
        client_uuid: str,
        desired_active: bool,
        user_id: int | None = None,
        sub_id: int | None = None,
    ) -> dict[int, bool]:
        results: dict[int, bool] = {}
        for srv_id, api_url, api_key in targets:
            try:
                async with XrayNodeClient(timeout=5.0) as client:
                    resp = await client.sync_client(
                        api_url=api_url,
                        api_key=api_key,
                        client_uuid=client_uuid,
                        is_active=desired_active,
                        service="vless",
                    )
                    verified = [
                        ib.lower() for ib in resp.verified_inbounds if isinstance(ib, str)
                    ] if resp.verified_inbounds else []
                    results[srv_id] = (
                        resp.result in ("applied", "already_newer")
                        and any("vless" in ib for ib in verified)
                    )
            except Exception as exc:
                logger.warning(
                    "Failed to sync VLESS user %s (sub %s) to server %s: %s",
                    user_id,
                    sub_id,
                    srv_id,
                    exc,
                )
                results[srv_id] = False
        return results

    @staticmethod
    async def _execute_deprovision_on_nodes(
        targets: list[tuple[int, str, str]],
        client_uuid: str,
    ) -> dict[int, bool]:
        results: dict[int, bool] = {}
        for srv_id, api_url, api_key in targets:
            try:
                async with XrayNodeClient(timeout=5.0) as client:
                    resp = await client.sync_client(
                        api_url=api_url,
                        api_key=api_key,
                        client_uuid=client_uuid,
                        is_active=False,
                        service="vless",
                    )
                    results[srv_id] = resp.result in ("applied", "already_newer")
            except Exception as exc:
                masked_uuid = f"{client_uuid[:8]}***" if client_uuid else "unknown"
                logger.warning(
                    "Failed to deprovision VLESS UUID %s from server %s: %s",
                    masked_uuid,
                    srv_id,
                    exc,
                )
                results[srv_id] = False
        return results

    @staticmethod
    async def sync_user_to_nodes(
        session: AsyncSession,
        user_id: int,
        is_active: bool | None = None,
    ) -> dict[int, bool]:
        """Provision or disable user UUID on all eligible exit nodes via Xray API."""
        sub = await vless_subscription_repo.get_subscription_by_user_id(session, user_id)
        if sub is None:
            return {}

        from services.subscription import SubscriptionService
        user = await session.get(User, user_id)

        # Database state is the ultimate source of truth
        desired_active = bool(
            sub.is_active
            and user is not None
            and not getattr(user, "is_banned", False)
            and not getattr(user, "financial_hold", False)
            and not getattr(user, "is_deleted", False)
            and SubscriptionService.check_vpn_access(user)
        )
        if is_active is False:
            desired_active = False

        servers = await VlessSubscriptionService.get_eligible_vless_servers(session)
        targets = VlessSubscriptionService._extract_node_targets(servers)
        results = await VlessSubscriptionService._execute_sync_to_nodes(
            targets, sub.uuid, desired_active, user_id=user_id, sub_id=sub.id
        )

        if any(results.values()):
            sub.last_synced_at = now_utc()
            await session.flush()

        return results

    # Backward compatibility alias
    sync_user_to_dual_nodes = sync_user_to_nodes

    @staticmethod
    async def deprovision_uuid_from_nodes(
        session: AsyncSession,
        client_uuid: str,
    ) -> dict[int, bool]:
        """Deprovision a revoked UUID across all eligible exit nodes."""
        if not client_uuid:
            return {}

        servers = await VlessSubscriptionService.get_eligible_vless_servers(session)
        targets = VlessSubscriptionService._extract_node_targets(servers)
        return await VlessSubscriptionService._execute_deprovision_on_nodes(targets, client_uuid)

    # Backward compatibility alias
    deprovision_uuid_from_dual_nodes = deprovision_uuid_from_nodes
