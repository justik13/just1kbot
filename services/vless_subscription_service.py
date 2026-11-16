"""Service for standard VLESS subscriptions, link generation, and node synchronization."""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import re
import urllib.parse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config.constants import (
    DEFAULT_WHITE_INTERNET_PATH,
    VALID_VLESS_TLS_FINGERPRINTS,
    VLESS_TLS_FINGERPRINT,
)
from config.enums import ServerHealthState, ServerLifecycleStatus
from database.models import Server, User, VlessSubscription, WhiteInternetSubscription
from database.repositories import vless_subscription_repo
from database.repositories.servers_repo import get_server_role
from services.xray_node_client import XrayNodeClient
from utils.datetime_helpers import now_utc
from utils.security import normalize_public_domain

logger = logging.getLogger(__name__)

_BACKGROUND_TASKS: set[asyncio.Task] = set()

_FQDN_REGEX = re.compile(
    r"^(?=.{1,253}$)(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}$"
)


class VlessSubscriptionService:
    """Business logic for standard VLESS subscriptions."""

    _ACTIVE_SERVER_SYNCS: set[int] = set()

    @staticmethod
    def _build_vless_fragment(
        flag: str | None,
        raw_name: str,
        badge_raw: str | None = None,
        *,
        suffix: str = "",
    ) -> str:
        clean_flag = (flag or "🌐").strip()
        clean_name = str(raw_name or "")
        for ch in ("?", "&", "\r", "\n"):
            clean_name = clean_name.replace(ch, " ")
        clean_name = clean_name.strip()
        if clean_flag and not clean_name.startswith(clean_flag):
            label = f"{clean_flag} {clean_name}".strip()
        else:
            label = clean_name
        if suffix and not label.endswith(suffix):
            label = f"{label} {suffix}".strip()

        quoted_label = urllib.parse.quote(label)
        # INCY renders vector flag when emoji is the first character after #
        # and displays serverDescription badge via ?serverDescription=base64(UTF-8)
        if badge_raw and str(badge_raw).strip() and str(badge_raw).strip().lower() != "none":
            clean_badge = str(badge_raw).strip()[:30]
            b64_badge = base64.b64encode(clean_badge.encode("utf-8")).decode("utf-8")
            return f"{quoted_label}?serverDescription={b64_badge}"
        return quoted_label

    @staticmethod
    def _resolve_port_and_fp(extra: dict) -> tuple[int, str]:
        raw_port = extra.get("vless_port", 443)
        try:
            port = int(raw_port) if raw_port is not None else 443
            if not (1 <= port <= 65535):
                port = 443
        except (ValueError, TypeError):
            port = 443

        raw_fp = str(extra.get("fp") or extra.get("fingerprint") or "").strip().lower()
        fp = raw_fp if raw_fp in VALID_VLESS_TLS_FINGERPRINTS else VLESS_TLS_FINGERPRINT
        return port, fp

    @staticmethod
    def _extract_sni_routes(extra: dict) -> list[dict[str, str]]:
        raw = extra.get("sni_routes")
        if not isinstance(raw, list):
            links_d = extra.get("links")
            if isinstance(links_d, dict):
                std_d = links_d.get("standard")
                if isinstance(std_d, dict):
                    raw = std_d.get("sni_routes")
        if not isinstance(raw, list):
            return []
        routes: list[dict[str, str]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            sni = str(item.get("sni") or "").strip().lower()
            bridge_code = str(item.get("bridge_code") or "").strip().lower()
            if sni and bridge_code and _FQDN_REGEX.match(sni):
                routes.append({"sni": sni, "bridge_code": bridge_code})
        return routes

    @staticmethod
    def generate_vless_links(
        subscription: VlessSubscription,
        servers: list[Server],
        exit_nodes: list[Server] | None = None,
    ) -> list[str]:
        """Generate VLESS TLS xtls-rprx-vision links.

        Never emits direct links to eu_hub or exit_node.
        For v2 ru_hubs, routes Standard traffic via ru_hub sni_routes (or exit_node.std_sni)
        on port 443 while displaying exit_node name and flag.
        """
        links: list[str] = []
        ru_hubs: list[Server] = []
        discovered_exits: list[Server] = []

        for srv in servers:
            if (
                not srv.is_active
                or srv.health_state != ServerHealthState.ONLINE
                or getattr(srv, "lifecycle_status", None) not in (None, ServerLifecycleStatus.ACTIVE)
            ):
                continue
            role = get_server_role(srv)
            if role == "ru_hub":
                ru_hubs.append(srv)
            elif role == "exit_node":
                discovered_exits.append(srv)

        active_exits: list[Server] = []
        exit_source = exit_nodes if exit_nodes is not None else discovered_exits
        for ex in exit_source:
            if (
                not ex.is_active
                or ex.health_state != ServerHealthState.ONLINE
                or getattr(ex, "lifecycle_status", None) not in (None, ServerLifecycleStatus.ACTIVE)
                or get_server_role(ex) != "exit_node"
            ):
                continue
            active_exits.append(ex)

        seen_links: set[str] = set()

        for hub in ru_hubs:
            hub_extra = hub.extra_data if isinstance(hub.extra_data, dict) else {}
            port, fp = VlessSubscriptionService._resolve_port_and_fp(hub_extra)
            sni_routes = VlessSubscriptionService._extract_sni_routes(hub_extra)
            route_by_code = {r["bridge_code"]: r["sni"] for r in sni_routes}

            if active_exits:
                for ex in active_exits:
                    ex_extra = ex.extra_data if isinstance(ex.extra_data, dict) else {}
                    if ex_extra.get("in_standard") is False:
                        continue
                    explicit_sni = str(ex_extra.get("std_sni") or "").strip().lower()
                    loc_code = str(ex_extra.get("location_code") or "").strip().lower()
                    target_sni = explicit_sni or route_by_code.get(loc_code, "")
                    if not target_sni or not _FQDN_REGEX.match(target_sni):
                        continue
                    fragment = VlessSubscriptionService._build_vless_fragment(
                        ex.country_flag,
                        ex_extra.get("vless_name") or ex.name,
                        ex_extra.get("server_description"),
                    )
                    link = (
                        f"vless://{subscription.uuid}@{target_sni}:{port}"
                        f"?encryption=none&security=tls&sni={target_sni}&fp={fp}"
                        f"&type=tcp&flow=xtls-rprx-vision#{fragment}"
                    )
                    if link not in seen_links:
                        seen_links.add(link)
                        links.append(link)
            elif sni_routes:
                for r in sni_routes:
                    target_sni = r["sni"]
                    fragment = VlessSubscriptionService._build_vless_fragment(
                        hub.country_flag,
                        r["bridge_code"].upper(),
                        hub_extra.get("server_description"),
                    )
                    link = (
                        f"vless://{subscription.uuid}@{target_sni}:{port}"
                        f"?encryption=none&security=tls&sni={target_sni}&fp={fp}"
                        f"&type=tcp&flow=xtls-rprx-vision#{fragment}"
                    )
                    if link not in seen_links:
                        seen_links.add(link)
                        links.append(link)
            else:
                domain = hub_extra.get("domain") or hub_extra.get("vless_domain")
                if domain:
                    domain = str(domain).strip().lower()
                    if _FQDN_REGEX.match(domain):
                        fragment = VlessSubscriptionService._build_vless_fragment(
                            hub.country_flag,
                            hub_extra.get("vless_name") or hub.name,
                            hub_extra.get("server_description"),
                        )
                        link = (
                            f"vless://{subscription.uuid}@{domain}:{port}"
                            f"?encryption=none&security=tls&sni={domain}&fp={fp}"
                            f"&type=tcp&flow=xtls-rprx-vision#{fragment}"
                        )
                        if link not in seen_links:
                            seen_links.add(link)
                            links.append(link)

        return links

    @staticmethod
    def generate_bi_xhttp_links(
        subscription: WhiteInternetSubscription,
        server: Server,
        exit_nodes: list[Server] | None = None,
        *,
        cdn_label_mode: str = "badge",
        cdn_yandex_label: str = "Yandex",
        cdn_turboflare_label: str = "Turboflare",
    ) -> list[str]:
        """Generate 3-layer XHTTP links for White Internet using xhttp_locations or legacy relays."""
        from services.white_internet_service import WhiteInternetService

        extra = server.extra_data if isinstance(server.extra_data, dict) else {}
        cdn_domains = extra.get("cdn_domains") if isinstance(extra.get("cdn_domains"), dict) else {}
        default_cdn = normalize_public_domain(
            cdn_domains.get("cdn_yandex")
            or extra.get("cdn_domain")
            or os.getenv("WHITE_INTERNET_CDN_DOMAIN")
        )
        turboflare_cdn = normalize_public_domain(
            cdn_domains.get("cdn_turboflare")
            or extra.get("turboflare_domain")
        )
        if not default_cdn and not turboflare_cdn:
            return []

        base_path = (
            extra.get("secret_base_path")
            or os.getenv("WHITE_INTERNET_PATH")
            or DEFAULT_WHITE_INTERNET_PATH
        )

        has_exit_filter = False
        wi_exit_names: dict[str, str] = {}
        wi_exit_badges: dict[str, str] = {}
        if exit_nodes:
            active_exits = [
                ex for ex in exit_nodes
                if (
                    ex.is_active
                    and ex.health_state == ServerHealthState.ONLINE
                    and getattr(ex, "lifecycle_status", None) in (None, ServerLifecycleStatus.ACTIVE)
                    and get_server_role(ex) == "exit_node"
                )
            ]
            if active_exits:
                has_exit_filter = True
                for ex in active_exits:
                    ex_extra = ex.extra_data if isinstance(ex.extra_data, dict) else {}
                    if not bool(ex_extra.get("in_white_internet", False)):
                        continue
                    loc_code = str(ex_extra.get("location_code") or "").strip().lower()
                    if not loc_code:
                        continue
                    flag = (ex.country_flag or "🌐").strip()
                    raw_name = str(ex_extra.get("vless_name") or ex.name or loc_code).strip()
                    for ch in ("?", "&", "\r", "\n"):
                        raw_name = raw_name.replace(ch, " ")
                    raw_name = raw_name.strip()
                    base_label = f"{flag} {raw_name}".strip() if (flag and not raw_name.startswith(flag)) else raw_name
                    label = base_label if base_label.endswith("(БИ)") else f"{base_label} (БИ)"
                    wi_exit_names[loc_code] = label
                    desc = ex_extra.get("server_description")
                    if desc and str(desc).strip():
                        wi_exit_badges[loc_code] = str(desc).strip()

        xhttp_locations = extra.get("xhttp_locations")
        if isinstance(xhttp_locations, list) and xhttp_locations:
            links: list[str] = []
            by_cdn: dict[tuple[str, str], list[dict]] = {}
            origin_entries: list[tuple[str, str, str]] = []
            seen_origins: set[tuple[str, str, str]] = set()
            for loc in xhttp_locations:
                if not isinstance(loc, dict) or not loc.get("path"):
                    continue
                loc_code = str(loc.get("location_code") or "").strip().lower()
                if has_exit_filter and loc_code and loc_code not in wi_exit_names:
                    continue
                cdn_hint = str(loc.get("cdn") or "").strip().lower()
                cdn_type = "cdn_turboflare" if cdn_hint == "cdn_turboflare" else "cdn_yandex"
                loc_cdn = turboflare_cdn if cdn_type == "cdn_turboflare" else default_cdn
                if not loc_cdn:
                    continue
                loc_path = str(loc["path"])
                if loc_code:
                    by_cdn.setdefault((cdn_type, loc_cdn), []).append({
                        "code": loc_code,
                        "path": loc_path,
                    })
                elif not has_exit_filter:
                    origin_key = (cdn_type, loc_cdn, loc_path)
                    if origin_key not in seen_origins:
                        seen_origins.add(origin_key)
                        origin_entries.append(origin_key)

            for (cdn_type, loc_cdn), cdn_relays in by_cdn.items():
                cdn_label = (
                    (cdn_turboflare_label or "Turboflare").strip()
                    if cdn_type == "cdn_turboflare"
                    else (cdn_yandex_label or "Yandex").strip()
                )
                eff_names = dict(wi_exit_names) if wi_exit_names else None
                eff_badges = dict(wi_exit_badges) if wi_exit_badges else {}
                if cdn_label and cdn_label_mode == "name" and eff_names:
                    eff_names = {k: f"{v} · {cdn_label}" for k, v in eff_names.items()}
                elif cdn_label and cdn_label_mode == "badge":
                    for k in (wi_exit_names or {r["code"]: r["code"] for r in cdn_relays}):
                        prev = eff_badges.get(k, "").strip()
                        eff_badges[k] = f"{prev} · {cdn_label}".strip(" ·") if prev else cdn_label
                links.extend(
                    WhiteInternetService.generate_vless_links(
                        subscription,
                        cdn_domain=loc_cdn,
                        path=base_path,
                        relays=cdn_relays,
                        include_origin=False,
                        relay_names=eff_names,
                        relay_badges=eff_badges or None,
                        cdn_type=cdn_type,
                    )
                )

            for cdn_type, loc_cdn, origin_path in origin_entries:
                links.extend(
                    WhiteInternetService.generate_vless_links(
                        subscription,
                        cdn_domain=loc_cdn,
                        path=base_path,
                        relays=None,
                        include_origin=True,
                        cdn_type=cdn_type,
                        origin_path=origin_path,
                    )
                )

            if not links and not has_exit_filter:
                if default_cdn:
                    links.extend(
                        WhiteInternetService.generate_vless_links(
                            subscription,
                            cdn_domain=default_cdn,
                            path=base_path,
                            relays=None,
                            include_origin=True,
                            cdn_type="cdn_yandex",
                        )
                    )
                if turboflare_cdn:
                    links.extend(
                        WhiteInternetService.generate_vless_links(
                            subscription,
                            cdn_domain=turboflare_cdn,
                            path=base_path,
                            relays=None,
                            include_origin=True,
                            cdn_type="cdn_turboflare",
                        )
                    )
            return links

        if has_exit_filter and not wi_exit_names:
            return []

        links = []
        if default_cdn:
            links.extend(
                WhiteInternetService.generate_vless_links(
                    subscription,
                    cdn_domain=default_cdn,
                    path=base_path,
                    relays=None,
                    include_origin=True,
                    cdn_type="cdn_yandex",
                )
            )
        if turboflare_cdn:
            links.extend(
                WhiteInternetService.generate_vless_links(
                    subscription,
                    cdn_domain=turboflare_cdn,
                    path=base_path,
                    relays=None,
                    include_origin=True,
                    cdn_type="cdn_turboflare",
                )
            )
        return links

    @staticmethod
    def build_subscription_url(token: str, domain: str | None = None) -> str:
        """Construct full public subscription URL for standard VLESS feed."""
        if not domain:
            try:
                from config.settings import get_settings
                settings = get_settings()
                domain = settings.SUBSCRIPTION_DOMAIN or settings.DOMAIN
            except Exception:
                domain = None
        if not domain:
            domain = os.getenv("SUBSCRIPTION_DOMAIN") or os.getenv("DOMAIN") or os.getenv("BOT_DOMAIN")
        if not domain:
            raise ValueError("No public DOMAIN configured for subscription URLs")
        clean_domain = domain.strip().replace("https://", "").replace("http://", "").rstrip("/")
        return f"https://{clean_domain}/sub/{token}"

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
    async def get_eligible_exit_nodes(
        session: AsyncSession,
    ) -> list[Server]:
        """Fetch all online active exit_node servers."""
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
        return [srv for srv in servers if get_server_role(srv) == "exit_node"]

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

        async def _sync_one(
            srv_id: int, api_url: str, api_key: str
        ) -> tuple[int, bool, bool]:
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

        tasks = [
            _sync_one(t[0], t[1], t[2])
            for t in targets
        ]
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

        async def _deprov_one(
            srv_id: int, api_url: str, api_key: str
        ) -> tuple[int, bool]:
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

        tasks = [
            _deprov_one(t[0], t[1], t[2])
            for t in targets
        ]
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

    @classmethod
    def sync_all_active_to_server_background(cls, server_id: int) -> asyncio.Task:
        """Spawn background server provisioning with GC task protection."""
        task = asyncio.create_task(cls.sync_all_active_to_server(server_id))
        _BACKGROUND_TASKS.add(task)
        task.add_done_callback(_BACKGROUND_TASKS.discard)
        return task
