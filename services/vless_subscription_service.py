"""Service for standard VLESS subscriptions, link generation, and node synchronization."""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import re
import urllib.parse

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from config.enums import ServerHealthState, ServerLifecycleStatus
from database.models import Server, User, VlessSubscription, VPNProfile
from database.repositories import vless_subscription_repo
from services.device_service import RESERVING_STATUSES
from services.xray_node_client import XrayNodeClient
from utils.datetime_helpers import now_utc

logger = logging.getLogger(__name__)

_BACKGROUND_TASKS: set[asyncio.Task] = set()

_FQDN_REGEX = re.compile(
    r"^(?=.{1,253}$)(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}$"
)


class VlessSubscriptionService:
    """Business logic for standard VLESS subscriptions."""

    _ACTIVE_SERVER_SYNCS: set[int] = set()

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
                logger.warning(
                    "Skipping server %s (%s) in VLESS links: no public TLS client domain configured",
                    srv.id,
                    srv.name,
                )
                continue

            domain = str(domain).strip().lower()
            if not domain or not _FQDN_REGEX.match(domain):
                logger.warning("Skipping server %s: invalid FQDN domain '%s'", srv.id, domain)
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
            # INCY renders vector flag when emoji is the first character after #
            # and displays serverDescription badge via ?serverDescription=base64(UTF-8)
            badge_raw = extra.get("origin_badge") if "origin_badge" in extra else extra.get("server_description")
            if badge_raw and str(badge_raw).strip() and str(badge_raw).strip().lower() != "none":
                clean_badge = str(badge_raw).strip()[:30]
                b64_badge = base64.b64encode(clean_badge.encode("utf-8")).decode("utf-8")
                fragment = f"{quoted_label}?serverDescription={b64_badge}"
            else:
                fragment = quoted_label

            link = (
                f"vless://{subscription.uuid}@{domain}:{port}"
                f"?encryption=none&security=tls&sni={domain}&alpn=http/1.1&fp=chrome"
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
            domain = os.getenv("DOMAIN") or os.getenv("BOT_DOMAIN")
        if not domain:
            raise ValueError("No public DOMAIN configured for subscription URLs")
        clean_domain = domain.strip().replace("https://", "").replace("http://", "").rstrip("/")
        return f"https://{clean_domain}/sub/access/{token}"

    @classmethod
    def ensure_synced_background(
        cls, user_id: int, is_active: bool | None = None, *, session: AsyncSession | None = None
    ) -> asyncio.Task | None:
        """Synchronize user on nodes with post-commit dispatch if session is active."""
        async def _run() -> None:
            from database.connection import session_scope
            try:
                async with session_scope() as scoped_session:
                    await cls.sync_user_to_nodes(scoped_session, user_id, is_active=is_active)

                    # Retry deprovisioning for any pending revoked UUIDs
                    sub = await vless_subscription_repo.get_subscription_by_user_id(scoped_session, user_id)
                    if sub and sub.pending_revoked_uuids:
                        servers = await cls.get_revocation_target_servers(scoped_session)
                        targets = cls._extract_node_targets(servers)
                        if targets:
                            sub_ver = getattr(sub, "version", 1) or 1
                            for rev_uuid in list(sub.pending_revoked_uuids):
                                deprov_res = await cls._execute_deprovision_on_nodes(targets, rev_uuid, version=sub_ver)
                                if (
                                    servers
                                    and len(targets) == len(servers)
                                    and all(deprov_res.values())
                                    and len(deprov_res) == len(targets)
                                ):
                                    await vless_subscription_repo.pop_pending_revoked_uuid(scoped_session, sub.id, rev_uuid)
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
        cls,
        old_uuid: str,
        *,
        version: int | None = None,
        sub_id: int | None = None,
        session: AsyncSession | None = None,
    ) -> asyncio.Task | None:
        """Deprovision user UUID from nodes with post-commit dispatch if session is active."""
        async def _run() -> None:
            from database.connection import session_scope
            try:
                targets = []
                async with session_scope() as scoped_session:
                    servers = await cls.get_revocation_target_servers(scoped_session)
                    targets = cls._extract_node_targets(servers)

                if targets:
                    results = await cls._execute_deprovision_on_nodes(targets, old_uuid, version=version)
                    if (
                        sub_id
                        and servers
                        and len(targets) == len(servers)
                        and all(results.values())
                        and len(results) == len(targets)
                    ):
                        async with session_scope() as scoped_session:
                            await vless_subscription_repo.pop_pending_revoked_uuid(scoped_session, sub_id, old_uuid)
                            sub = await scoped_session.get(VlessSubscription, sub_id)
                            if sub and not sub.is_active and version is not None:
                                sub.last_synced_at = now_utc()
                                sub.last_synced_version = max(getattr(sub, "last_synced_version", 0) or 0, version)
                                await scoped_session.flush()
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
        from database.repositories.servers_repo import is_vless_capable_server

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
        return [srv for srv in servers if is_vless_capable_server(srv)]

    @staticmethod
    async def get_configured_vless_servers(
        session: AsyncSession,
    ) -> list[Server]:
        """Fetch all configured active servers capable of serving VLESS TLS regardless of transient health state.

        Used for synchronization and deprovisioning to guarantee that offline or degraded nodes
        are not silently omitted from revocation/sync targets.
        """
        from database.repositories.servers_repo import is_vless_capable_server

        stmt = (
            select(Server)
            .where(
                Server.is_active.is_(True),
                Server.lifecycle_status == ServerLifecycleStatus.ACTIVE,
            )
            .order_by(Server.id.asc())
        )
        servers = (await session.execute(stmt)).scalars().all()
        return [srv for srv in servers if is_vless_capable_server(srv)]

    @staticmethod
    async def get_revocation_target_servers(
        session: AsyncSession,
    ) -> list[Server]:
        """Fetch all servers that might still host VLESS credentials for revocation.
        Includes servers where is_active=False, provided they are still active machines
        (lifecycle_status == ServerLifecycleStatus.ACTIVE).
        """
        from database.repositories.servers_repo import is_vless_capable_server

        stmt = (
            select(Server)
            .where(
                Server.lifecycle_status == ServerLifecycleStatus.ACTIVE,
            )
            .order_by(Server.id.asc())
        )
        servers = (await session.execute(stmt)).scalars().all()
        return [srv for srv in servers if is_vless_capable_server(srv)]

    @staticmethod
    def _extract_node_targets(servers: list[Server]) -> list[tuple[int, str, str]]:
        from database.repositories.servers_repo import get_server_xray_api_url

        targets: list[tuple[int, str, str]] = []
        for srv in servers:
            api_url = get_server_xray_api_url(srv)
            api_key = srv.api_key
            if not api_url:
                logger.warning(
                    "Skipping server %s (%s) for VLESS sync: missing explicit Xray API endpoint",
                    srv.id,
                    srv.name,
                )
                continue
            if not api_key:
                logger.warning(
                    "Skipping server %s (%s) for VLESS sync: missing API key",
                    srv.id,
                    srv.name,
                )
                continue
            targets.append((srv.id, api_url, api_key))
        return targets

    @staticmethod
    async def _execute_sync_to_nodes(
        targets: list[tuple[int, str, str]],
        client_uuid: str,
        desired_active: bool,
        version: int | None = None,
        user_id: int | None = None,
        sub_id: int | None = None,
    ) -> dict[int, bool]:
        results, _ = await VlessSubscriptionService._execute_sync_to_nodes_with_status(
            targets, client_uuid, desired_active, version=version, user_id=user_id, sub_id=sub_id
        )
        return results

    @staticmethod
    async def _execute_sync_to_nodes_with_status(
        targets: list[tuple[int, str, str]],
        client_uuid: str,
        desired_active: bool,
        version: int | None = None,
        user_id: int | None = None,
        sub_id: int | None = None,
    ) -> tuple[dict[int, bool], bool]:
        if not targets:
            return {}, False

        async def _sync_one(srv_id: int, api_url: str, api_key: str) -> tuple[int, bool, bool]:
            needs_bump = False
            try:
                async with XrayNodeClient(timeout=5.0) as client:
                    resp = await client.sync_client(
                        api_url=api_url,
                        api_key=api_key,
                        client_uuid=client_uuid,
                        is_active=desired_active,
                        version=version,
                        idempotency_key=f"vless:{sub_id or user_id or client_uuid[:8]}:{version}:{desired_active}",
                        service="vless",
                    )
                    verified = [
                        ib.lower() for ib in resp.verified_inbounds if isinstance(ib, str)
                    ] if resp.verified_inbounds else []
                    has_vless = any("vless" in ib for ib in verified)
                    if resp.result == "applied":
                        ok = has_vless and bool(resp.all_inbounds_verified)
                        return srv_id, ok, False
                    elif resp.result == "already_newer":
                        expected_state = "active" if desired_active else "disabled"
                        actual_state = (resp.raw_data or {}).get("state")
                        match = (actual_state == expected_state)
                        if not match:
                            needs_bump = True
                        ok = (
                            has_vless
                            and match
                            and bool(resp.all_inbounds_verified)
                        )
                        return srv_id, ok, needs_bump
                    else:
                        return srv_id, False, False
            except Exception as exc:
                logger.warning(
                    "Failed to sync VLESS user %s (sub %s) to server %s: %s",
                    user_id,
                    sub_id,
                    srv_id,
                    exc,
                )
                return srv_id, False, False

        tasks = [_sync_one(srv_id, api_url, api_key) for srv_id, api_url, api_key in targets]
        res_list = await asyncio.gather(*tasks)

        results: dict[int, bool] = {}
        needs_version_bump = False
        for srv_id, ok, bump in res_list:
            results[srv_id] = ok
            if bump:
                needs_version_bump = True

        return results, needs_version_bump

    @staticmethod
    async def _execute_deprovision_on_nodes(
        targets: list[tuple[int, str, str]],
        client_uuid: str,
        version: int | None = None,
    ) -> dict[int, bool]:
        if not targets:
            return {}

        async def _deprov_one(srv_id: int, api_url: str, api_key: str) -> tuple[int, bool]:
            try:
                async with XrayNodeClient(timeout=5.0) as client:
                    resp = await client.sync_client(
                        api_url=api_url,
                        api_key=api_key,
                        client_uuid=client_uuid,
                        is_active=False,
                        version=version,
                        idempotency_key=f"vless:deprov:{client_uuid}:{version}",
                        service="vless",
                    )
                    verified = [
                        ib.lower() for ib in resp.verified_inbounds if isinstance(ib, str)
                    ] if resp.verified_inbounds else []
                    has_vless = any("vless" in ib for ib in verified)
                    if resp.result == "applied":
                        return srv_id, has_vless and bool(resp.all_inbounds_verified)
                    elif resp.result == "already_newer":
                        actual_state = (resp.raw_data or {}).get("state")
                        return srv_id, (
                            has_vless
                            and (actual_state == "disabled")
                            and bool(resp.all_inbounds_verified)
                        )
                    else:
                        return srv_id, False
            except Exception as exc:
                masked_uuid = f"{client_uuid[:8]}***" if client_uuid else "unknown"
                logger.warning(
                    "Failed to deprovision VLESS UUID %s from server %s: %s",
                    masked_uuid,
                    srv_id,
                    exc,
                )
                return srv_id, False

        tasks = [_deprov_one(srv_id, api_url, api_key) for srv_id, api_url, api_key in targets]
        res_list = await asyncio.gather(*tasks)
        return dict(res_list)

    @staticmethod
    async def sync_user_to_nodes(
        session: AsyncSession,
        user_id: int,
        is_active: bool | None = None,
    ) -> dict[int, bool]:
        """Provision or disable user UUID on all configured exit nodes via Xray API."""
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

        if desired_active:
            servers = await VlessSubscriptionService.get_configured_vless_servers(session)
        else:
            servers = await VlessSubscriptionService.get_revocation_target_servers(session)
        targets = VlessSubscriptionService._extract_node_targets(servers)
        if not targets:
            return {}

        sub_version = getattr(sub, "version", 1) or 1
        results, needs_version_bump = await VlessSubscriptionService._execute_sync_to_nodes_with_status(
            targets, sub.uuid, desired_active, version=sub_version, user_id=user_id, sub_id=sub.id
        )

        # If node reported version conflict, bump version and retry once
        if needs_version_bump:
            locked_sub = await vless_subscription_repo.bump_subscription_version(session, sub.id)
            if locked_sub:
                sub = locked_sub
                sub_version = locked_sub.version
            else:
                sub.version = sub_version + 1
                await session.flush()
                sub_version = sub.version
            results, _ = await VlessSubscriptionService._execute_sync_to_nodes_with_status(
                targets, sub.uuid, desired_active, version=sub_version, user_id=user_id, sub_id=sub.id
            )

        if (
            targets
            and len(targets) == len(servers)
            and all(results.values())
            and len(results) == len(targets)
        ):
            sub.last_synced_at = now_utc()
            sub.last_synced_version = sub_version
            await session.flush()

        return results

    @staticmethod
    async def deprovision_uuid_from_nodes(
        session: AsyncSession,
        client_uuid: str,
        version: int | None = None,
    ) -> dict[int, bool]:
        """Deprovision a revoked UUID across all configured exit nodes."""
        if not client_uuid:
            return {}

        servers = await VlessSubscriptionService.get_revocation_target_servers(session)
        targets = VlessSubscriptionService._extract_node_targets(servers)
        if not targets:
            return {}
        return await VlessSubscriptionService._execute_deprovision_on_nodes(targets, client_uuid, version=version)

    @classmethod
    async def sync_all_active_to_server(cls, server_id: int) -> int:
        """Provisions all active VLESS subscriptions onto a newly added/updated node."""
        from database.connection import session_scope
        from database.repositories.servers_repo import is_vless_capable_server
        from services.subscription import SubscriptionService

        srv_id = int(server_id)
        if srv_id in cls._ACTIVE_SERVER_SYNCS:
            logger.info("Server %s is already being synchronized; skipping duplicate invocation", srv_id)
            return 0
        cls._ACTIVE_SERVER_SYNCS.add(srv_id)

        synced_count = 0
        try:
            async with session_scope() as session:
                server = await session.get(Server, srv_id)
                if not server or not server.is_active or not is_vless_capable_server(server):
                    return 0
                target = cls._extract_node_targets([server])
                if not target:
                    return 0

            # Pre-flight health check to avoid sequential timeout storms on unreachable nodes
            srv_tuple = target[0]
            try:
                from services.xray_node_client import XrayNodeClient
                async with XrayNodeClient(timeout=3.0, max_retries=1) as client:
                    is_healthy, err, _ = await client.check_health(srv_tuple[1], srv_tuple[2])
                    if not is_healthy:
                        logger.warning(
                            "Server %s is unhealthy during bootstrap pre-flight (%s); deferring sweep",
                            srv_id,
                            err,
                        )
                        return 0
            except Exception as e:
                logger.warning(
                    "Server %s is unreachable during bootstrap pre-flight (%s); deferring sweep",
                    srv_id,
                    e,
                )
                return 0

            async with session_scope() as session:
                stmt = (
                    select(VlessSubscription, User)
                    .join(User, User.id == VlessSubscription.user_id)
                    .where(
                        VlessSubscription.is_active.is_(True),
                        User.is_deleted.is_(False),
                        User.is_banned.is_(False),
                        User.financial_hold.is_(False),
                    )
                )
                res = await session.execute(stmt)
                rows = res.all()

            total_eligible = 0
            has_failures = False
            for sub, user in rows:
                if not SubscriptionService.check_vpn_access(user):
                    continue
                total_eligible += 1
                sub_ver = getattr(sub, "version", 1) or 1
                try:
                    res_map = await cls._execute_sync_to_nodes(
                        target, sub.uuid, True, version=sub_ver, user_id=user.id, sub_id=sub.id
                    )
                    if res_map.get(srv_id) is True:
                        synced_count += 1
                    else:
                        has_failures = True
                except Exception as e:
                    has_failures = True
                    logger.warning("Failed to provision sub_id=%s on new server %s: %s", sub.id, srv_id, e)

            # Record synchronized epoch in server extra_data ONLY if all eligible subscriptions succeeded without failures
            if not has_failures and synced_count == total_eligible:
                async with session_scope() as session:
                    server = await session.get(Server, srv_id)
                    if server and server.xray_instance_epoch:
                        extra = dict(server.extra_data or {})
                        extra["vless_synced_epoch"] = server.xray_instance_epoch
                        server.extra_data = extra
                        await session.flush()
            else:
                logger.warning(
                    "Bootstrap partial for server %s: synced %d of %d eligible (failures=%s). Will retry on next sweep.",
                    srv_id,
                    synced_count,
                    total_eligible,
                    has_failures,
                )
        except Exception as e:
            logger.error("Error in sync_all_active_to_server for server %s: %s", srv_id, e)
        finally:
            cls._ACTIVE_SERVER_SYNCS.discard(srv_id)

        logger.info("Provisioned %d active VLESS subscriptions on new server %s", synced_count, srv_id)
        return synced_count
