"""Periodic health, disk usage (>85%), and availability monitor for VPN nodes.

Unified Server Health Monitor with persistent state machine:
ONLINE -> WAITING_CONFIRMATION -> PROBLEM -> ONLINE (or MANUAL_DISABLED)
"""
import asyncio
import logging
import os
import socket
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

import aiohttp
from aiogram import Bot
from sqlalchemy import func, select
from sqlalchemy.orm.attributes import flag_modified

from bot.keyboards.notifications import get_admin_node_keyboard
from bot.texts.backend import TIME_HOURS_LEFT
from bot.texts.runtime.alerts import (
    ALERT_BRIDGE_TSPU_PROBLEM,
    ALERT_BRIDGE_TSPU_PROBLEM_REMINDER,
    ALERT_BRIDGE_TSPU_RESTORED,
    ALERT_CDN_INGRESS_PROBLEM,
    ALERT_CDN_INGRESS_PROBLEM_REMINDER,
    ALERT_CDN_INGRESS_RESTORED,
    ALERT_INGRESS_ERR_TIMEOUT,
    ALERT_INGRESS_PROBLEM,
    ALERT_INGRESS_PROBLEM_REMINDER,
    ALERT_INGRESS_RESTORED,
    ALERT_SERVER_DISK_CRITICAL,
    ALERT_SERVER_HOSTING_TRAFFIC_CRITICAL,
    ALERT_SERVER_HOSTING_TRAFFIC_RESTORED,
    ALERT_SERVER_HOSTING_TRAFFIC_WARN,
    ALERT_SERVER_LAST_APPLY_FAILED,
    ALERT_SERVER_OUTAGE_REMINDER,
    ALERT_SERVER_PROBLEM,
    ALERT_SERVER_RESTORED,
    ALERT_SERVER_SSL_CERT_EXPIRING,
)
from bot.texts.runtime.notifications import (
    TIME_HOURS_MINUTES_FORMAT,
    TIME_MINUTES_FORMAT,
)
from config.constants import (
    ServerHealthState,
    WHITE_INTERNET_CAPABILITY,
    WHITE_INTERNET_SUB_PATH_PREFIX,
)
from config.enums import ServerLifecycleStatus
from config.settings import get_settings
from database.connection import session_scope
from database.models import Server
from database.repositories.servers_repo import (
    derive_v2_capabilities,
    get_all_servers,
    get_server_by_id,
    get_server_role,
    get_server_xray_api_url,
    is_v2_node_server,
    is_vless_capable_server,
    is_vless_sync_target_server,
    is_white_internet_hub_server,
    merge_v2_extra_data,
    update_server,
    update_server_health_snapshot,
    update_server_xray_epoch_cas,
)
from utils.datetime_helpers import now_utc
from utils.telegram import safe, safe_send_message
from utils.traffic_helpers import get_server_hosting_usage

logger = logging.getLogger(__name__)

CHECK_INTERVAL_SECONDS = 60.0  # Частота тиков фонового воркера (1 минута)
CONFIRMATION_DELAY_SECONDS = 60.0  # Пауза между FAIL #1 и FAIL #2 (неблокирующая)
REQUIRED_STABLE_SUCCESSES = 3  # 3 успешных ответа подряд для подтверждения восстановления
DISK_ALERT_COOLDOWN_SECONDS = 3600.0  # 1 час между повторными уведомлениями о диске
REQUIRED_INGRESS_FAILS = 3  # Требуется 3 цикла сбоя подряд (>= 3 мин) перед отправкой алерта
REQUIRED_CDN_FAILS = 5  # Требуется 5 циклов сбоя подряд (~5 мин) для фильтрации трансграничного шума
REQUIRED_INGRESS_SUCCESSES = 2  # Требуется 2 цикла успеха подряд для подтверждения восстановления
REQUIRED_BRIDGE_FAILS = 2  # Требуется 2 цикла сбоя моста подряд (~2 мин) перед переключением и алертом
REQUIRED_BRIDGE_SUCCESSES = 2  # Требуется 2 цикла успеха моста подряд для возврата в топологию
INGRESS_RETRY_DELAY = 1.5  # Базовая задержка перед повторным запросом зонда при ошибке/таймауте
INGRESS_PROBE_TOTAL_TIMEOUT = 30.0  # Максимальное время синтетического ingress-зонда
INGRESS_PROBE_CONNECT_TIMEOUT = 25.0  # Запас на трансграничный TLS handshake при пиках нагрузки
OUTAGE_REMINDER_INTERVAL_SECONDS = 3600.0  # 1 час между повторными напоминаниями о продолжающейся аварии
# A hung node can hold a healthcheck for tens of seconds; checking servers in
# bounded parallel batches keeps one degraded node from freezing alerts for
# every other node in the cycle.
CHECK_PARALLELISM = 6


def _build_v2_extra_patch(
    existing_extra: dict[str, Any] | None,
    status_data: dict[str, Any] | None,
    health_data: dict[str, Any] | None,
) -> dict[str, Any]:
    """Compute changed v2 discovery and health metadata keys without clobbering probe state."""
    base = dict(existing_extra) if isinstance(existing_extra, dict) else {}
    merged = merge_v2_extra_data(base, status_data, health_data)
    if isinstance(health_data, dict):
        if isinstance(health_data.get("system_load"), dict):
            merged["system_load"] = health_data["system_load"]
        if "cdn_domain" in health_data and health_data["cdn_domain"] is None:
            merged["cdn_domain"] = ""
        if "sub_path_prefix" in health_data and not health_data["sub_path_prefix"]:
            if "sub_path_prefix" in base:
                merged["sub_path_prefix"] = base["sub_path_prefix"]
            else:
                merged.pop("sub_path_prefix", None)

    has_reported_role = (
        (isinstance(status_data, dict) and status_data.get("role") is not None)
        or (isinstance(health_data, dict) and health_data.get("role") is not None)
    )
    if not has_reported_role:
        if "role" in base:
            merged["role"] = base["role"]
        else:
            merged.pop("role", None)

    patch: dict[str, Any] = {}
    for k, v in merged.items():
        if k not in base or base[k] != v:
            patch[k] = v
    for k in ("last_apply", "ssl_cert"):
        if k in base and k not in merged:
            patch[k] = None
    return patch


def _format_outage_duration(seconds: float) -> str:
    total_mins = max(1, int(seconds // 60))
    hours = total_mins // 60
    minutes = total_mins % 60
    if hours > 0 and minutes > 0:
        return TIME_HOURS_MINUTES_FORMAT.format(hours=hours, minutes=minutes)
    if hours > 0:
        return TIME_HOURS_LEFT.format(hours=hours)
    return TIME_MINUTES_FORMAT.format(minutes=minutes)


class ServerMonitorState:
    def __init__(self, server_id: int):
        self.server_id = server_id
        self.health_state = ServerHealthState.ONLINE
        self.consecutive_fails = 0
        self.consecutive_successes = 0
        self.problem_started_at: float | None = None
        self.next_check_at: float | None = None
        self.last_check_monotonic: float = 0.0
        self.last_alert_sent_state: str | None = None
        self.disk_alert_last_sent: float | None = None
        self.last_core_reminder_at: float | None = None
        self.ingress_problem: bool = False
        self.consecutive_ingress_fails: int = 0
        self.consecutive_ingress_successes: int = 0
        self.ingress_problem_started_at: float | None = None
        self.last_ingress_reminder_at: float | None = None
        self.cdn_problem: bool = False
        self.consecutive_cdn_fails: int = 0
        self.consecutive_cdn_successes: int = 0
        self.cdn_problem_started_at: float | None = None
        self.last_cdn_reminder_at: float | None = None

    def sync_from_db_server(self, db_server: Server):
        if db_server:
            if not db_server.is_active:
                self.health_state = ServerHealthState.MANUAL_DISABLED
            else:
                self.health_state = db_server.health_state or ServerHealthState.ONLINE

            self.consecutive_fails = db_server.consecutive_fails or 0
            self.consecutive_successes = db_server.consecutive_successes or 0
            self.last_alert_sent_state = db_server.last_alert_sent_state
            if isinstance(getattr(db_server, "extra_data", None), dict):
                self.ingress_problem = bool(db_server.extra_data.get("ingress_problem", False))
                self.consecutive_ingress_fails = int(db_server.extra_data.get("consecutive_ingress_fails", 0) or 0)
                self.consecutive_ingress_successes = int(db_server.extra_data.get("consecutive_ingress_successes", 0) or 0)
                self.cdn_problem = bool(db_server.extra_data.get("cdn_problem", False))
                self.consecutive_cdn_fails = int(db_server.extra_data.get("consecutive_cdn_fails", 0) or 0)
                self.consecutive_cdn_successes = int(db_server.extra_data.get("consecutive_cdn_successes", 0) or 0)
                saved_ing_start = db_server.extra_data.get("ingress_problem_started_at")
                if saved_ing_start is not None and isinstance(saved_ing_start, (int, float)):
                    now_m = time.monotonic()
                    diff = max(0.0, time.time() - float(saved_ing_start))
                    self.ingress_problem_started_at = now_m - diff
                else:
                    self.ingress_problem_started_at = None
                saved_cdn_start = db_server.extra_data.get("cdn_problem_started_at")
                if saved_cdn_start is not None and isinstance(saved_cdn_start, (int, float)):
                    now_m = time.monotonic()
                    diff = max(0.0, time.time() - float(saved_cdn_start))
                    self.cdn_problem_started_at = now_m - diff
                else:
                    self.cdn_problem_started_at = None
            else:
                self.ingress_problem = False
                self.consecutive_ingress_fails = 0
                self.consecutive_ingress_successes = 0
                self.ingress_problem_started_at = None
                self.last_ingress_reminder_at = None
                self.cdn_problem = False
                self.consecutive_cdn_fails = 0
                self.consecutive_cdn_successes = 0
                self.cdn_problem_started_at = None
                self.last_cdn_reminder_at = None

            if db_server.problem_started_at:
                now_m = time.monotonic()
                diff = (now_utc() - db_server.problem_started_at).total_seconds()
                self.problem_started_at = now_m - max(0.0, diff)
            else:
                self.problem_started_at = None

            if db_server.next_check_at:
                now_m = time.monotonic()
                rem = (db_server.next_check_at - now_utc()).total_seconds()
                self.next_check_at = now_m + max(0.0, rem)
            else:
                self.next_check_at = None


_monitor_states: dict[int, ServerMonitorState] = {}
_server_states = _monitor_states


def get_server_monitor_state(server_id: int, server: Server | None = None) -> ServerMonitorState:
    if server_id not in _monitor_states:
        st = ServerMonitorState(server_id)
        if server:
            st.sync_from_db_server(server)
        _monitor_states[server_id] = st
    return _monitor_states[server_id]


def reset_server_monitor_state(server_id: int, new_state: str = ServerHealthState.ONLINE) -> None:
    st = get_server_monitor_state(server_id)
    st.health_state = new_state
    st.consecutive_fails = 0
    st.consecutive_successes = 0
    st.problem_started_at = None
    st.next_check_at = None
    st.last_alert_sent_state = None
    st.disk_alert_last_sent = None
    st.last_core_reminder_at = None
    st.ingress_problem = False
    st.consecutive_ingress_fails = 0
    st.consecutive_ingress_successes = 0
    st.ingress_problem_started_at = None
    st.last_ingress_reminder_at = None
    st.cdn_problem = False
    st.consecutive_cdn_fails = 0
    st.consecutive_cdn_successes = 0
    st.cdn_problem_started_at = None
    st.last_cdn_reminder_at = None


def clear_server_monitor_state(server_id: int) -> None:
    """Evict server monitor state from memory on server deletion."""
    _monitor_states.pop(server_id, None)


async def _send_admin_alert_msg(bot: Bot, text: str, reply_markup=None) -> bool:
    settings = get_settings()
    admin_ids = settings.ADMIN_IDS
    if not admin_ids:
        return False

    success = False
    for admin_id in admin_ids:
        try:
            msg_id = await safe_send_message(
                bot,
                chat_id=admin_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode="HTML",
            )
            if msg_id:
                success = True
        except Exception as exc:
            logger.warning("Failed to send node monitor alert to admin %s: %s", admin_id, exc)
    return success


async def _probe_http_endpoint(
    session: aiohttp.ClientSession,
    probe_url: str,
    *,
    xhttp_mode: bool = False,
) -> tuple[bool, str]:
    """Synthetic HTTP probe; for XHTTP routes any non-404 status below 500 confirms origin Xray reachability."""
    for attempt in range(3):
        try:
            async with session.get(
                probe_url,
                allow_redirects=False,
            ) as probe_resp:
                if xhttp_mode:
                    if 200 <= probe_resp.status < 500 and probe_resp.status != 404:
                        return (True, str(probe_resp.status))
                elif probe_resp.status == 200:
                    return (True, "200")
                if attempt < 2 and probe_resp.status in (502, 503, 504):
                    await asyncio.sleep(INGRESS_RETRY_DELAY * (attempt + 1))
                    continue
                return (False, str(probe_resp.status))
        except Exception as probe_exc:
            if attempt < 2:
                await asyncio.sleep(INGRESS_RETRY_DELAY * (attempt + 1))
                continue
            err_msg = str(probe_exc).strip()
            if not err_msg:
                if isinstance(probe_exc, (asyncio.TimeoutError, TimeoutError)):
                    err_msg = ALERT_INGRESS_ERR_TIMEOUT
                else:
                    err_msg = type(probe_exc).__name__
            return (False, err_msg)
    return (False, "UNKNOWN")


async def _probe_single_ingress_endpoint(
    session: aiohttp.ClientSession,
    domain: str,
    sub_prefix: str,
) -> tuple[bool, str]:
    """Synthetic HTTP probe for a single White Internet subscription endpoint."""
    return await _probe_http_endpoint(session, f"https://{domain}{sub_prefix}/ping", xhttp_mode=False)


def _collect_xhttp_paths_by_cdn(extra_dict: dict[str, Any]) -> list[tuple[str, str]]:
    """Collect one representative (cdn_type, normalized_path) pair per configured CDN type."""
    xhttp_locations = extra_dict.get("xhttp_locations")
    if not isinstance(xhttp_locations, list):
        return []
    result: list[tuple[str, str]] = []
    seen_types: set[str] = set()
    for loc in xhttp_locations:
        if not isinstance(loc, dict) or not loc.get("path"):
            continue
        cdn_hint = str(loc.get("cdn") or "").strip().lower()
        cdn_type = "cdn_turboflare" if cdn_hint == "cdn_turboflare" else "cdn_yandex"
        if cdn_type in seen_types:
            continue
        seen_types.add(cdn_type)
        result.append((cdn_type, "/" + str(loc["path"]).lstrip("/")))
    return result


def _collect_origin_xhttp_paths(extra_dict: dict[str, Any]) -> list[str]:
    """Collect distinct representative XHTTP paths served by this node."""
    return list(dict.fromkeys(path for _, path in _collect_xhttp_paths_by_cdn(extra_dict)))


def _collect_cdn_probe_targets(
    extra_dict: dict[str, Any],
    origin_domain: str | None,
    primary_cdn: str | None,
    sub_prefix: str,
) -> list[tuple[str, str, bool]]:
    """Collect distinct (domain, path, is_xhttp) CDN probe targets for a WI hub."""
    cdn_domains_map = extra_dict.get("cdn_domains") if isinstance(extra_dict.get("cdn_domains"), dict) else {}
    yandex_cdn = str(cdn_domains_map.get("cdn_yandex") or primary_cdn or "").strip()
    turbo_cdn = str(cdn_domains_map.get("cdn_turboflare") or extra_dict.get("turboflare_domain") or "").strip()

    targets: list[tuple[str, str, bool]] = []
    seen: set[tuple[str, str, bool]] = set()

    def _add(dom: str, path: str, is_xhttp: bool) -> None:
        clean_dom = str(dom or "").strip()
        if not clean_dom or clean_dom == origin_domain:
            return
        clean_path = "/" + str(path or "").lstrip("/")
        key = (clean_dom, clean_path, is_xhttp)
        if key not in seen:
            seen.add(key)
            targets.append(key)

    for dom in (primary_cdn or "", yandex_cdn, turbo_cdn):
        if dom:
            _add(dom, f"{sub_prefix}/ping", False)

    for cdn_type, clean_path in _collect_xhttp_paths_by_cdn(extra_dict):
        loc_cdn = turbo_cdn if cdn_type == "cdn_turboflare" else (yandex_cdn or str(primary_cdn or "").strip())
        if loc_cdn:
            _add(loc_cdn, clean_path, True)

    return targets


async def _run_topology_sync_with_retry_tracking(context: str) -> None:
    """Run nonblocking v2 topology sync and log retry status (tracked centrally in topology_sync_service)."""
    try:
        from services.topology_sync_service import sync_v2_topology_to_nodes_nonblocking

        results = await sync_v2_topology_to_nodes_nonblocking()
        failed_ids = [sid for sid, ok in results.items() if not ok]
        if failed_ids:
            logger.warning(
                "v2 topology sync (%s) had failed nodes %s; retry armed for next monitor cycle.",
                context,
                failed_ids,
            )
    except Exception as topo_exc:
        logger.warning(
            "v2 topology sync (%s) failed (%s); retry armed for next monitor cycle.",
            context,
            topo_exc,
        )


def _build_bridge_problem_alert(
    ru_srv: Server,
    eu_srv: Server,
    eu_ip: str,
    err_detail: str,
    remaining_eu: int,
) -> str:
    """Format ALERT_BRIDGE_TSPU_PROBLEM with consistent fallback wording."""
    action_text = (
        f"Трафик автоматически переключён на резервный шлюз (доступно: {remaining_eu})."
        if remaining_eu >= 1
        else "Резервных шлюзов нет — маршрут оставлен в качестве fallback."
    )
    return ALERT_BRIDGE_TSPU_PROBLEM.format(
        ru_name=safe(ru_srv.name),
        ru_id=ru_srv.id,
        eu_name=safe(eu_srv.name),
        eu_id=eu_srv.id,
        eu_ip=safe(eu_ip),
        error_detail=safe(err_detail),
        action_text=action_text,
    )


def _build_bridge_reminder_alert(
    ru_srv: Server,
    eu_srv: Server,
    eu_ip: str,
    err_detail: str,
    started_at: float,
    now_wall: float,
) -> str:
    """Format ALERT_BRIDGE_TSPU_PROBLEM_REMINDER with outage duration."""
    return ALERT_BRIDGE_TSPU_PROBLEM_REMINDER.format(
        ru_name=safe(ru_srv.name),
        ru_id=ru_srv.id,
        eu_name=safe(eu_srv.name),
        eu_id=eu_srv.id,
        eu_ip=safe(eu_ip),
        error_detail=safe(err_detail),
        hours_text=_format_outage_duration(max(60.0, now_wall - started_at)),
    )


async def _try_reenable_hosting_limit_server(
    bot: Bot,
    server: Server,
    st: ServerMonitorState,
) -> tuple[bool, ServerMonitorState, bool]:
    """Re-enable a node disabled for HOSTING_TRAFFIC_LIMIT once a new billing cycle < 90% starts.

    Returns (re_enabled, monitor_state, topology_dirty).
    """
    if getattr(server, "disabled_reason", None) != "HOSTING_TRAFFIC_LIMIT":
        return False, st, False

    extra = dict(server.extra_data or {})
    usage = get_server_hosting_usage(extra)
    alert_cycle = extra.get("hosting_alert_cycle")
    if not (
        usage.limit_gb is not None
        and alert_cycle
        and usage.current_cycle != alert_cycle
        and usage.pct < 90.0
    ):
        return False, st, False

    re_enabled = False
    async with session_scope() as session:
        fresh = await get_server_by_id(session, server.id, for_update=True)
        if (
            fresh
            and not fresh.is_active
            and getattr(fresh, "disabled_reason", None) == "HOSTING_TRAFFIC_LIMIT"
        ):
            fresh_extra = dict(fresh.extra_data or {})
            fresh_usage = get_server_hosting_usage(fresh_extra)
            fresh_alert_cycle = fresh_extra.get("hosting_alert_cycle")
            if (
                fresh_usage.limit_gb is not None
                and fresh_alert_cycle
                and fresh_usage.current_cycle != fresh_alert_cycle
                and fresh_usage.pct < 90.0
            ):
                fresh_extra["hosting_alert_level"] = 80 if fresh_usage.pct >= 80.0 else 0
                fresh_extra["hosting_alert_cycle"] = fresh_usage.current_cycle
                await update_server(
                    session,
                    fresh,
                    is_active=True,
                    disabled_reason=None,
                    disabled_at=None,
                    health_state=ServerHealthState.ONLINE,
                    consecutive_fails=0,
                    consecutive_successes=0,
                    problem_started_at=None,
                    next_check_at=None,
                    last_alert_sent_state=None,
                    extra_data=fresh_extra,
                )
                server.is_active = True
                server.disabled_reason = None
                server.disabled_at = None
                server.health_state = ServerHealthState.ONLINE
                server.extra_data = fresh_extra
                reset_server_monitor_state(server.id, ServerHealthState.ONLINE)
                st = get_server_monitor_state(server.id, server)
                re_enabled = True

    if re_enabled:
        try:
            await _send_admin_alert_msg(
                bot,
                ALERT_SERVER_HOSTING_TRAFFIC_RESTORED.format(
                    server_name=safe(server.name),
                    cycle=usage.current_cycle,
                ),
                reply_markup=get_admin_node_keyboard(server.id),
            )
        except Exception as exc:
            logger.error(
                "Failed to deliver hosting traffic recovery alert for server %d: %s",
                server.id,
                exc,
            )
    return re_enabled, st, re_enabled


async def _evaluate_ru_hub_bridge_health(
    bot: Bot,
    server: Server,
    bridge_health_map: dict[str, Any],
    eu_hubs_by_id: dict[int, Server],
    eu_hubs_by_ip: dict[str, Server],
    eu_hubs_by_domain: dict[str, Server],
    all_enabled_eu_ids: set[int],
) -> bool:
    """Persist per-bridge TSPU block/recovery state for a healthy ru_hub and dispatch alerts.

    Returns True if blocked_eu_ids changed and topology sync is needed.
    """
    topology_dirty = False
    bridge_alerts_to_send: list[tuple[str, str, str, float | None]] = []
    bridge_lock_key = 9_000_000_000 + int(server.id)

    async with session_scope() as session:
        bind = getattr(session, "bind", None)
        if not bind and hasattr(session, "sync_session"):
            bind = getattr(session.sync_session, "bind", None)
        if bind and getattr(bind, "dialect", None) and getattr(bind.dialect, "name", None) == "postgresql":
            got_bridge_lock = bool(await session.scalar(select(func.pg_try_advisory_xact_lock(bridge_lock_key))))
        else:
            got_bridge_lock = True

        if got_bridge_lock:
            fresh_ru = await get_server_by_id(session, server.id, for_update=True)
            if fresh_ru is None:
                logger.debug(
                    "ru_hub %d was deleted during health check; skipping bridge TSPU state/alerts.",
                    server.id,
                )
            else:
                ru_extra = dict(fresh_ru.extra_data or {})
                bh_state: dict[str, dict[str, Any]] = dict(ru_extra.get("bridge_health_state") or {})
                prev_blocked = {int(x) for x in (ru_extra.get("blocked_eu_ids") or []) if str(x).isdigit()}
                new_blocked = {eid for eid in prev_blocked if eid in all_enabled_eu_ids}
                bh_changed = new_blocked != prev_blocked
                now_wall = time.time()

                # Prune state entries only for deleted/admin-disabled eu_hubs (not transient WAITING_CONFIRMATION)
                for stale_k in list(bh_state.keys()):
                    if not stale_k.isdigit() or int(stale_k) not in all_enabled_eu_ids:
                        bh_state.pop(stale_k, None)
                        bh_changed = True

                def _count_remaining_online_eus() -> int:
                    live_online = {
                        eid
                        for eid, esrv in eu_hubs_by_id.items()
                        if eid in all_enabled_eu_ids
                        and esrv.is_active
                        and esrv.health_state == ServerHealthState.ONLINE
                        and get_server_monitor_state(eid, esrv).health_state == ServerHealthState.ONLINE
                    }
                    return len(live_online - new_blocked)

                def _maybe_queue_reminder(entry: dict[str, Any], eu_key: str, eu_srv: Server, eu_ip: str, err_detail: str) -> None:
                    last_rem = entry.get("last_reminder_at")
                    if isinstance(last_rem, (int, float)) and (now_wall - float(last_rem)) >= OUTAGE_REMINDER_INTERVAL_SECONDS:
                        started = float(entry.get("started_at") or last_rem)
                        bridge_alerts_to_send.append((
                            "reminder",
                            eu_key,
                            _build_bridge_reminder_alert(server, eu_srv, eu_ip, err_detail, started, now_wall),
                            now_wall,
                        ))

                for eu_ip, probe_info in bridge_health_map.items():
                    if not isinstance(probe_info, dict):
                        continue
                    eu_srv = eu_hubs_by_ip.get(str(eu_ip).strip())
                    if eu_srv is None:
                        probe_sni = str(probe_info.get("sni") or "").strip().lower().rstrip(".")
                        if probe_sni:
                            eu_srv = eu_hubs_by_domain.get(probe_sni)
                    if (
                        not eu_srv
                        or eu_srv.id not in all_enabled_eu_ids
                        or not eu_srv.is_active
                        or eu_srv.health_state != ServerHealthState.ONLINE
                        or get_server_monitor_state(eu_srv.id, eu_srv).health_state != ServerHealthState.ONLINE
                    ):
                        continue

                    eu_key = str(eu_srv.id)
                    entry = dict(bh_state.get(eu_key) or {})
                    was_blocked = bool(entry.get("blocked", False))
                    probe_ok = bool(probe_info.get("ok", False))
                    err_detail = str(probe_info.get("error") or "probe failed")
                    raw_checked_at = probe_info.get("checked_at")
                    probe_checked_at = float(raw_checked_at) if isinstance(raw_checked_at, (int, float)) else None
                    is_duplicate_sample = (
                        probe_checked_at is not None
                        and entry.get("last_checked_at") == probe_checked_at
                    )

                    if is_duplicate_sample:
                        if was_blocked:
                            new_blocked.add(eu_srv.id)
                            if entry.get("problem_alert_sent") is False:
                                bridge_alerts_to_send.append((
                                    "problem",
                                    eu_key,
                                    _build_bridge_problem_alert(
                                        server, eu_srv, eu_ip, err_detail, _count_remaining_online_eus()
                                    ),
                                    now_wall,
                                ))
                            else:
                                _maybe_queue_reminder(entry, eu_key, eu_srv, eu_ip, err_detail)
                        continue

                    if probe_checked_at is not None:
                        entry["last_checked_at"] = probe_checked_at

                    if not probe_ok:
                        fails = int(entry.get("fails", 0) or 0) + 1
                        entry["fails"] = fails
                        entry["successes"] = 0
                        entry["last_error"] = err_detail
                        bh_changed = True

                        if not was_blocked and fails >= REQUIRED_BRIDGE_FAILS:
                            entry["blocked"] = True
                            entry["started_at"] = now_wall
                            entry["problem_alert_sent"] = False
                            entry["last_reminder_at"] = None
                            new_blocked.add(eu_srv.id)
                            bridge_alerts_to_send.append((
                                "problem",
                                eu_key,
                                _build_bridge_problem_alert(
                                    server, eu_srv, eu_ip, err_detail, _count_remaining_online_eus()
                                ),
                                now_wall,
                            ))
                        elif was_blocked:
                            new_blocked.add(eu_srv.id)
                            if entry.get("problem_alert_sent") is False:
                                bridge_alerts_to_send.append((
                                    "problem",
                                    eu_key,
                                    _build_bridge_problem_alert(
                                        server, eu_srv, eu_ip, err_detail, _count_remaining_online_eus()
                                    ),
                                    now_wall,
                                ))
                            else:
                                last_rem = entry.get("last_reminder_at")
                                if not isinstance(last_rem, (int, float)):
                                    entry["last_reminder_at"] = now_wall
                                    bh_changed = True
                                else:
                                    _maybe_queue_reminder(entry, eu_key, eu_srv, eu_ip, err_detail)
                    else:
                        succs = int(entry.get("successes", 0) or 0) + 1
                        entry["successes"] = succs
                        entry["fails"] = 0
                        entry["last_error"] = None
                        if was_blocked and succs >= REQUIRED_BRIDGE_SUCCESSES:
                            entry["blocked"] = False
                            entry["started_at"] = None
                            entry["last_reminder_at"] = None
                            entry.pop("problem_alert_sent", None)
                            new_blocked.discard(eu_srv.id)
                            bridge_alerts_to_send.append((
                                "restored",
                                eu_key,
                                ALERT_BRIDGE_TSPU_RESTORED.format(
                                    ru_name=safe(server.name),
                                    ru_id=server.id,
                                    eu_name=safe(eu_srv.name),
                                    eu_id=eu_srv.id,
                                    eu_ip=safe(eu_ip),
                                ),
                                None,
                            ))
                        elif was_blocked:
                            new_blocked.add(eu_srv.id)
                        bh_changed = True

                    bh_state[eu_key] = entry

                if bh_changed and fresh_ru:
                    sorted_blocked = sorted(new_blocked)
                    if sorted_blocked != sorted(prev_blocked):
                        topology_dirty = True
                    ru_extra["bridge_health_state"] = bh_state
                    if sorted_blocked:
                        ru_extra["blocked_eu_ids"] = sorted_blocked
                    else:
                        ru_extra.pop("blocked_eu_ids", None)
                    await update_server(session, fresh_ru, extra_data=ru_extra)
                    server.extra_data = ru_extra

    sent_bridge_timestamps: list[tuple[str, str, float]] = []
    for alert_kind, eu_key, b_alert, reminder_ts in bridge_alerts_to_send:
        try:
            sent_ok = await _send_admin_alert_msg(
                bot,
                b_alert,
                reply_markup=get_admin_node_keyboard(server.id),
            )
            if sent_ok:
                if alert_kind in ("problem", "reminder") and reminder_ts is not None:
                    sent_bridge_timestamps.append((alert_kind, eu_key, reminder_ts))
            else:
                logger.warning(
                    "Bridge TSPU %s alert not delivered for ru_hub %d (eu=%s); will retry.",
                    alert_kind,
                    server.id,
                    eu_key,
                )
        except Exception as exc:
            logger.error("Failed to deliver bridge TSPU alert for ru_hub %d: %s", server.id, exc)

    if sent_bridge_timestamps:
        try:
            async with session_scope() as session:
                fresh_ru_ack = await get_server_by_id(session, server.id, for_update=True)
                if fresh_ru_ack is not None:
                    ack_extra = dict(fresh_ru_ack.extra_data or {})
                    ack_bh = dict(ack_extra.get("bridge_health_state") or {})
                    ack_changed = False
                    for alert_kind, eu_key, reminder_ts in sent_bridge_timestamps:
                        if eu_key in ack_bh and isinstance(ack_bh[eu_key], dict):
                            ack_entry = dict(ack_bh[eu_key])
                            ack_entry["last_reminder_at"] = reminder_ts
                            if alert_kind == "problem":
                                ack_entry["problem_alert_sent"] = True
                            ack_bh[eu_key] = ack_entry
                            ack_changed = True
                    if ack_changed:
                        ack_extra["bridge_health_state"] = ack_bh
                        await update_server(session, fresh_ru_ack, extra_data=ack_extra)
                        server.extra_data = ack_extra
        except Exception as ack_exc:
            logger.warning(
                "Failed to persist bridge alert timestamps for ru_hub %d: %s",
                server.id,
                ack_exc,
            )

    return topology_dirty


async def check_node_resources_and_alerts(bot: Bot):
    async with session_scope() as session:
        servers = await get_all_servers(session)

    if not servers:
        _monitor_states.clear()
        return

    # Auto-prune monitor states for servers no longer present in DB
    active_ids = {s.id for s in servers}
    for sid in list(_monitor_states.keys()):
        if sid not in active_ids:
            _monitor_states.pop(sid, None)

    from services.topology_sync_service import (
        _extract_domain,
        _extract_ipv4,
        is_topology_retry_pending,
    )

    now_m = time.monotonic()
    topology_dirty = is_topology_retry_pending()

    eu_hubs_by_id: dict[int, Server] = {}
    eu_hubs_by_ip: dict[str, Server] = {}
    eu_hubs_by_domain: dict[str, Server] = {}
    all_enabled_eu_ids: set[int] = set()
    for _srv in servers:
        if get_server_role(_srv) == "eu_hub":
            eu_hubs_by_id[_srv.id] = _srv
            _ip = _extract_ipv4(_srv)
            if _ip:
                eu_hubs_by_ip[_ip] = _srv
            _dom = _extract_domain(_srv)
            if _dom:
                eu_hubs_by_domain[_dom] = _srv
            if _srv.is_active and getattr(_srv, "lifecycle_status", None) in (None, ServerLifecycleStatus.ACTIVE):
                all_enabled_eu_ids.add(_srv.id)
    probe_timeout = aiohttp.ClientTimeout(
        total=INGRESS_PROBE_TOTAL_TIMEOUT,
        connect=INGRESS_PROBE_CONNECT_TIMEOUT,
    )
    probe_connector = aiohttp.TCPConnector(family=socket.AF_INET)
    shared_probe_sess = aiohttp.ClientSession(timeout=probe_timeout, connector=probe_connector)

    async def _check_one(server):
        nonlocal topology_dirty
        st = get_server_monitor_state(server.id, server)

        # 1. Если сервер отключён — проверяем причину; при HOSTING_TRAFFIC_LIMIT и новом цикле (< 90%) включаем обратно
        if not server.is_active:
            _re_enabled, st, dirty_delta = await _try_reenable_hosting_limit_server(bot, server, st)
            if dirty_delta:
                topology_dirty = True
            if not server.is_active:
                st.health_state = ServerHealthState.MANUAL_DISABLED
                return

        # Если сервер был отключён, а теперь активен — переводим в ONLINE
        if st.health_state == ServerHealthState.MANUAL_DISABLED:
            st.health_state = ServerHealthState.ONLINE
            st.consecutive_fails = 0
            st.consecutive_successes = 0
            st.problem_started_at = None
            st.next_check_at = None
            st.last_alert_sent_state = None

        # 2. Неблокирующая проверка временных интервалов (WAITING_CONFIRMATION)
        if st.health_state == ServerHealthState.WAITING_CONFIRMATION:
            if st.next_check_at and now_m < st.next_check_at:
                return

        # Захватываем ожидаемое состояние ТОЧНО перед выполнением сетевой проверки
        expected_health_state = st.health_state
        expected_consecutive_fails = st.consecutive_fails
        expected_consecutive_successes = st.consecutive_successes

        st.last_check_monotonic = now_m
        caps = getattr(server, "capabilities", None) or []
        if not isinstance(caps, (list, tuple, set)):
            caps = []

        has_xray = (
            is_vless_capable_server(server)
            or is_white_internet_hub_server(server)
            or is_v2_node_server(server)
        )

        if not has_xray:
            logger.warning(
                "Server %s (%s) has unsupported or unassigned protocol '%s', skipping healthcheck",
                server.id, server.name, getattr(server, "protocol", "unknown"),
            )
            return

        is_xray_node = has_xray

        # 4a. Исполнение проверки Core Node API с гарантированным отловом любых сетевых ошибок/таймаутов
        is_healthy = False
        xray_epoch = None
        xray_data = None
        xray_ok = True

        if has_xray:
            try:
                from services.xray_node_client import XrayNodeClient

                xray_url = get_server_xray_api_url(server)
                if not xray_url:
                    logger.warning(
                        "Xray capability advertised but no valid Xray API URL for server %s (%s)",
                        server.id,
                        server.name,
                    )
                    xray_ok = False
                else:
                    async with XrayNodeClient(
                        timeout=10.0,
                    ) as xray_client:
                        xray_ok, xray_epoch, xray_data = await xray_client.check_health(
                            xray_url, server.api_key
                        )
            except Exception as exc:
                logger.warning(
                    "Xray healthcheck exception for server %s (%s): %s",
                    server.id, server.name, exc,
                )
                xray_ok = False

        traffic_limit_exceeded = bool(
            isinstance(xray_data, dict)
            and isinstance(xray_data.get("traffic_limit"), dict)
            and xray_data["traffic_limit"].get("exceeded")
        )
        if traffic_limit_exceeded:
            logger.warning(
                "Server %s (%s) exceeded hosting traffic limit; marking unhealthy in bot",
                server.id,
                server.name,
            )
            xray_ok = False

        if has_xray:
            is_healthy = xray_ok
        else:
            is_healthy = False

        # 4b. Изолированная синтетическая проверка Ingress (публичный CDN / Nginx шлюза)
        # Сбои на синтетическом эндпоинте не аффектят статус ядра и выполняются независимо.
        origin_probe_result: tuple[bool, str] | None = None
        cdn_probe_result: tuple[bool, str] | None = None
        origin_domain: str | None = None
        cdn_domain: str | None = None
        origin_probe_endpoint: str | None = None
        cdn_probe_domain: str | None = None
        cdn_probe_endpoint: str | None = None
        raw_sub_prefix = None
        if isinstance(getattr(server, "extra_data", None), dict):
            raw_sub_prefix = server.extra_data.get("sub_path_prefix")
        sub_prefix = (
            raw_sub_prefix if isinstance(raw_sub_prefix, str) and raw_sub_prefix.strip()
            else (os.getenv("WHITE_INTERNET_SUB_PATH_PREFIX") or WHITE_INTERNET_SUB_PATH_PREFIX)
        ).strip().rstrip("/")
        if not sub_prefix.startswith("/"):
            sub_prefix = f"/{sub_prefix}"

        if is_xray_node:
            try:
                node_role = get_server_role(server)
                extra_dict = server.extra_data if isinstance(getattr(server, "extra_data", None), dict) else {}
                has_bot_proxy = bool(extra_dict.get("bot_domain")) or bool((extra_dict.get("modules") or {}).get("bot_proxy"))
                is_wi_hub = has_bot_proxy and node_role not in ("eu_hub", "exit_node") and (
                    node_role == "ru_hub"
                    or WHITE_INTERNET_CAPABILITY in (getattr(server, "capabilities", None) or [])
                    or bool(extra_dict.get("cdn_domain"))
                    or bool(extra_dict.get("cdn_domains"))
                )
                if is_wi_hub:
                    origin_domain = extra_dict.get("domain")
                    cdn_domain = extra_dict.get("cdn_domain")
                    if not origin_domain and server.api_url:
                        parsed = urlsplit(server.api_url)
                        if parsed.hostname and not parsed.hostname.replace(".", "").isdigit() and parsed.hostname != "localhost":
                            origin_domain = parsed.hostname

                    if origin_domain:
                        origin_domain = str(origin_domain).strip()
                    if cdn_domain:
                        cdn_domain = str(cdn_domain).strip()

                    cdn_targets = _collect_cdn_probe_targets(
                        extra_dict, origin_domain, cdn_domain, sub_prefix
                    )
                    if origin_domain and cdn_targets:
                        # Dual probe: probe Origin (/ping + XHTTP routes) and all configured CDN targets in parallel
                        origin_paths: list[tuple[str, bool]] = [(f"{sub_prefix}/ping", False)]
                        for xp in _collect_origin_xhttp_paths(extra_dict):
                            origin_paths.append((xp, True))
                        origin_tasks = [
                            _probe_http_endpoint(
                                shared_probe_sess,
                                f"https://{origin_domain}{path}",
                                xhttp_mode=is_xhttp,
                            )
                            for path, is_xhttp in origin_paths
                        ]
                        cdn_tasks = [
                            _probe_http_endpoint(
                                shared_probe_sess,
                                f"https://{dom}{path}",
                                xhttp_mode=is_xhttp,
                            )
                            for dom, path, is_xhttp in cdn_targets
                        ]
                        probe_results = await asyncio.gather(*origin_tasks, *cdn_tasks)
                        origin_results = probe_results[:len(origin_paths)]
                        cdn_results = probe_results[len(origin_paths):]
                        failed_origin_pair = next(
                            (
                                (op_path, res)
                                for (op_path, _), res in zip(origin_paths, origin_results, strict=True)
                                if not res[0]
                            ),
                            None,
                        )
                        if failed_origin_pair is not None:
                            f_op_path, (_, f_op_detail) = failed_origin_pair
                            origin_probe_endpoint = f_op_path
                            origin_probe_result = (False, f_op_detail)
                        else:
                            origin_probe_endpoint = f"{sub_prefix}/ping"
                            origin_probe_result = (True, origin_results[0][1])

                        failed_cdn_pair = next(
                            (
                                (tgt, res)
                                for tgt, res in zip(cdn_targets, cdn_results, strict=True)
                                if not res[0]
                            ),
                            None,
                        )
                        if failed_cdn_pair is not None:
                            (f_dom, f_path, _), (_, f_detail) = failed_cdn_pair
                            cdn_probe_domain = f_dom
                            cdn_probe_endpoint = f_path
                            cdn_probe_result = (False, f_detail)
                        else:
                            cdn_probe_domain = cdn_domain or cdn_targets[0][0]
                            cdn_probe_endpoint = f"{sub_prefix}/ping"
                            cdn_probe_result = (True, cdn_results[0][1])
                    else:
                        target_domain = cdn_domain or origin_domain
                        if target_domain:
                            origin_probe_result = await _probe_single_ingress_endpoint(
                                shared_probe_sess, target_domain, sub_prefix
                            )
                            cdn_probe_result = None
            except Exception as outer_probe_exc:
                logger.warning(
                    "Unexpected error preparing ingress probe for server %s: %s",
                    server.id, outer_probe_exc,
                )

        alerts_to_send: list[dict] = []
        ingress_alerts_to_send: list[tuple[str, str, bool, float | None]] = []

        # Decoupled ingress alert evaluation and delivery (independent of core server CAS / health state)
        if origin_probe_result is not None:
            lock_key = 8_000_000_000 + int(server.id)

            async with session_scope() as session:
                bind = getattr(session, "bind", None)
                if not bind and hasattr(session, "sync_session"):
                    bind = getattr(session.sync_session, "bind", None)
                if bind and getattr(bind, "dialect", None) and getattr(bind.dialect, "name", None) == "postgresql":
                    got_lock = bool(await session.scalar(select(func.pg_try_advisory_xact_lock(lock_key))))
                    if not got_lock:
                        logger.debug("Ingress/relay alert evaluation for server %d locked by peer worker. Skipping alert dispatch.", server.id)
                else:
                    got_lock = True

                if got_lock:
                    fresh_server = await get_server_by_id(session, server.id, for_update=True)
                    if fresh_server is None:
                        logger.debug(
                            "Server %d was deleted during ingress probe; skipping ingress state/alerts.",
                            server.id,
                        )
                    else:
                        db_extra = dict(fresh_server.extra_data or {})
                        db_ingress_problem = bool(db_extra.get("ingress_problem"))
                        db_cdn_problem = bool(db_extra.get("cdn_problem"))

                        # Durable-by-Default: sync in-memory state with DB
                        if db_ingress_problem:
                            st.ingress_problem = True
                        if "consecutive_ingress_fails" in db_extra:
                            st.consecutive_ingress_fails = int(db_extra.get("consecutive_ingress_fails") or 0)
                        if "consecutive_ingress_successes" in db_extra:
                            st.consecutive_ingress_successes = int(db_extra.get("consecutive_ingress_successes") or 0)
                        saved_ing_start = db_extra.get("ingress_problem_started_at")
                        if saved_ing_start is not None and isinstance(saved_ing_start, (int, float)):
                            if st.ingress_problem_started_at is None:
                                diff = max(0.0, time.time() - float(saved_ing_start))
                                st.ingress_problem_started_at = now_m - diff
                        elif not db_ingress_problem and not st.ingress_problem:
                            st.ingress_problem_started_at = None

                        if db_cdn_problem:
                            st.cdn_problem = True
                        if "consecutive_cdn_fails" in db_extra:
                            st.consecutive_cdn_fails = int(db_extra.get("consecutive_cdn_fails") or 0)
                        if "consecutive_cdn_successes" in db_extra:
                            st.consecutive_cdn_successes = int(db_extra.get("consecutive_cdn_successes") or 0)
                        saved_cdn_start = db_extra.get("cdn_problem_started_at")
                        if saved_cdn_start is not None and isinstance(saved_cdn_start, (int, float)):
                            if st.cdn_problem_started_at is None:
                                diff = max(0.0, time.time() - float(saved_cdn_start))
                                st.cdn_problem_started_at = now_m - diff
                        elif not db_cdn_problem and not st.cdn_problem:
                            st.cdn_problem_started_at = None

                        has_ingress_problem = db_ingress_problem or st.ingress_problem
                        has_cdn_problem = db_cdn_problem or st.cdn_problem
                        extra_changed = False

                        if origin_probe_result is not None:
                            origin_ok, origin_detail = origin_probe_result
                            active_origin_domain = origin_domain or cdn_domain or server.name
                            effective_origin_endpoint = origin_probe_endpoint or f"{sub_prefix}/ping"

                            # 1. Evaluate Origin Ingress
                            if not origin_ok:
                                st.consecutive_ingress_fails += 1
                                st.consecutive_ingress_successes = 0
                                if (
                                    db_extra.get("consecutive_ingress_fails") != st.consecutive_ingress_fails
                                    or db_extra.get("consecutive_ingress_successes") != 0
                                ):
                                    db_extra["consecutive_ingress_fails"] = st.consecutive_ingress_fails
                                    db_extra["consecutive_ingress_successes"] = 0
                                    extra_changed = True

                                if not has_ingress_problem and st.consecutive_ingress_fails >= REQUIRED_INGRESS_FAILS:
                                    st.ingress_problem = True
                                    st.ingress_problem_started_at = now_m
                                    db_extra["ingress_problem"] = True
                                    db_extra["ingress_problem_started_at"] = time.time()
                                    extra_changed = True
                                    ingress_alerts_to_send.append((
                                        "ingress",
                                        ALERT_INGRESS_PROBLEM.format(
                                            server_name=safe(server.name),
                                            server_id=server.id,
                                            domain=safe(active_origin_domain),
                                            status_or_err=safe(origin_detail),
                                            endpoint=safe(effective_origin_endpoint),
                                        ),
                                        True,
                                        now_m,
                                    ))
                                elif has_ingress_problem:
                                    if st.last_ingress_reminder_at is None:
                                        st.last_ingress_reminder_at = now_m
                                    elif (now_m - st.last_ingress_reminder_at) >= OUTAGE_REMINDER_INTERVAL_SECONDS:
                                        outage_dur = (now_m - st.ingress_problem_started_at) if st.ingress_problem_started_at else OUTAGE_REMINDER_INTERVAL_SECONDS
                                        ingress_alerts_to_send.append((
                                            "ingress",
                                            ALERT_INGRESS_PROBLEM_REMINDER.format(
                                                server_name=safe(server.name),
                                                server_id=server.id,
                                                domain=safe(active_origin_domain),
                                                endpoint=safe(effective_origin_endpoint),
                                                hours_text=_format_outage_duration(outage_dur),
                                            ),
                                            True,
                                            now_m,
                                        ))
                            else:
                                st.consecutive_ingress_successes += 1
                                st.consecutive_ingress_fails = 0
                                if (
                                    db_extra.get("consecutive_ingress_successes") != st.consecutive_ingress_successes
                                    or db_extra.get("consecutive_ingress_fails") != 0
                                ):
                                    db_extra["consecutive_ingress_successes"] = st.consecutive_ingress_successes
                                    db_extra["consecutive_ingress_fails"] = 0
                                    extra_changed = True

                                if st.consecutive_ingress_successes >= REQUIRED_INGRESS_SUCCESSES:
                                    st.ingress_problem = False
                                    st.ingress_problem_started_at = None
                                    st.last_ingress_reminder_at = None
                                    if "ingress_problem" in db_extra or "ingress_problem_started_at" in db_extra:
                                        db_extra.pop("ingress_problem", None)
                                        db_extra.pop("ingress_problem_started_at", None)
                                        extra_changed = True
                                    if has_ingress_problem:
                                        ingress_alerts_to_send.append((
                                            "ingress",
                                            ALERT_INGRESS_RESTORED.format(
                                                server_name=safe(server.name),
                                                server_id=server.id,
                                                domain=safe(active_origin_domain),
                                                endpoint=safe(effective_origin_endpoint),
                                            ),
                                            False,
                                            None,
                                        ))

                            # 2. Evaluate CDN Delivery (only in dual probe mode when origin is healthy)
                            effective_cdn_domain = cdn_probe_domain or cdn_domain
                            effective_cdn_endpoint = cdn_probe_endpoint or f"{sub_prefix}/ping"
                            if cdn_probe_result is not None and effective_cdn_domain:
                                cdn_ok, cdn_detail = cdn_probe_result
                                if origin_ok:
                                    if cdn_ok:
                                        st.consecutive_cdn_successes += 1
                                        st.consecutive_cdn_fails = 0
                                        if (
                                            db_extra.get("consecutive_cdn_successes") != st.consecutive_cdn_successes
                                            or db_extra.get("consecutive_cdn_fails") != 0
                                        ):
                                            db_extra["consecutive_cdn_successes"] = st.consecutive_cdn_successes
                                            db_extra["consecutive_cdn_fails"] = 0
                                            extra_changed = True

                                        if st.consecutive_cdn_successes >= REQUIRED_INGRESS_SUCCESSES:
                                            st.cdn_problem = False
                                            st.cdn_problem_started_at = None
                                            st.last_cdn_reminder_at = None
                                            if "cdn_problem" in db_extra or "cdn_problem_started_at" in db_extra:
                                                db_extra.pop("cdn_problem", None)
                                                db_extra.pop("cdn_problem_started_at", None)
                                                extra_changed = True
                                            if has_cdn_problem:
                                                ingress_alerts_to_send.append((
                                                    "cdn",
                                                    ALERT_CDN_INGRESS_RESTORED.format(
                                                        server_name=safe(server.name),
                                                        server_id=server.id,
                                                        domain=safe(effective_cdn_domain),
                                                        endpoint=safe(effective_cdn_endpoint),
                                                    ),
                                                    False,
                                                    None,
                                                ))
                                    else:
                                        st.consecutive_cdn_fails += 1
                                        st.consecutive_cdn_successes = 0
                                        if (
                                            db_extra.get("consecutive_cdn_fails") != st.consecutive_cdn_fails
                                            or db_extra.get("consecutive_cdn_successes") != 0
                                        ):
                                            db_extra["consecutive_cdn_fails"] = st.consecutive_cdn_fails
                                            db_extra["consecutive_cdn_successes"] = 0
                                            extra_changed = True

                                        if st.consecutive_cdn_fails < REQUIRED_CDN_FAILS:
                                            logger.info(
                                                "Xray origin node %s Origin (%s) is healthy (200), but CDN (%s %s) returned %s (%d/%d fails). Suppressing transient alert.",
                                                server.id, active_origin_domain, effective_cdn_domain, effective_cdn_endpoint, cdn_detail, st.consecutive_cdn_fails, REQUIRED_CDN_FAILS,
                                            )
                                        elif not has_cdn_problem:
                                            st.cdn_problem = True
                                            st.cdn_problem_started_at = now_m
                                            db_extra["cdn_problem"] = True
                                            db_extra["cdn_problem_started_at"] = time.time()
                                            extra_changed = True
                                            logger.warning(
                                                "Xray origin node %s CDN (%s %s) persistent failure (%d fails): %s (Origin %s is 200 OK). Sending CDN alert.",
                                                server.id, effective_cdn_domain, effective_cdn_endpoint, st.consecutive_cdn_fails, cdn_detail, active_origin_domain,
                                            )
                                            ingress_alerts_to_send.append((
                                                "cdn",
                                                ALERT_CDN_INGRESS_PROBLEM.format(
                                                    server_name=safe(server.name),
                                                    server_id=server.id,
                                                    domain=safe(effective_cdn_domain),
                                                    origin_domain=safe(active_origin_domain),
                                                    status_or_err=safe(cdn_detail),
                                                    endpoint=safe(effective_cdn_endpoint),
                                                ),
                                                True,
                                                now_m,
                                            ))
                                        elif has_cdn_problem:
                                            if st.last_cdn_reminder_at is None:
                                                st.last_cdn_reminder_at = now_m
                                            elif (now_m - st.last_cdn_reminder_at) >= OUTAGE_REMINDER_INTERVAL_SECONDS:
                                                outage_dur = (now_m - st.cdn_problem_started_at) if st.cdn_problem_started_at else OUTAGE_REMINDER_INTERVAL_SECONDS
                                                ingress_alerts_to_send.append((
                                                    "cdn",
                                                    ALERT_CDN_INGRESS_PROBLEM_REMINDER.format(
                                                        server_name=safe(server.name),
                                                        server_id=server.id,
                                                        domain=safe(effective_cdn_domain),
                                                        endpoint=safe(effective_cdn_endpoint),
                                                        hours_text=_format_outage_duration(outage_dur),
                                                    ),
                                                    True,
                                                    now_m,
                                                ))

                        if extra_changed and fresh_server:
                            merged_extra = dict(fresh_server.extra_data or {})
                            merged_extra.update(db_extra)
                            for _health_key in (
                                "ingress_problem",
                                "ingress_problem_started_at",
                                "cdn_problem",
                                "cdn_problem_started_at",
                            ):
                                if _health_key not in db_extra:
                                    merged_extra.pop(_health_key, None)
                            await update_server(session, fresh_server, extra_data=merged_extra)
                            server.extra_data = merged_extra

            # Decoupled alert dispatch outside DB transaction and advisory lock (notifications only)
            for alert_kind, alert_text, is_problem, reminder_ts in ingress_alerts_to_send:
                try:
                    sent_ok = await _send_admin_alert_msg(
                        bot,
                        alert_text,
                        reply_markup=get_admin_node_keyboard(server.id),
                    )
                    if sent_ok and is_problem and reminder_ts is not None:
                        if alert_kind == "ingress":
                            st.last_ingress_reminder_at = reminder_ts
                        elif alert_kind == "cdn":
                            st.last_cdn_reminder_at = reminder_ts
                    elif not sent_ok:
                        logger.warning(
                            "Failed to deliver %s alert for server %d (send returned False)",
                            alert_kind,
                            server.id,
                        )
                except Exception as e:
                    logger.error("Failed to deliver %s alert for server %d: %s", alert_kind, server.id, e)

        if is_healthy and get_server_role(server) == "ru_hub" and isinstance(xray_data, dict) and isinstance(xray_data.get("bridge_health"), dict):
            if await _evaluate_ru_hub_bridge_health(
                bot,
                server,
                xray_data["bridge_health"],
                eu_hubs_by_id,
                eu_hubs_by_ip,
                eu_hubs_by_domain,
                all_enabled_eu_ids,
            ):
                topology_dirty = True

        hosting_auto_disabled = False
        if is_healthy:
            if st.health_state == ServerHealthState.WAITING_CONFIRMATION:
                # Восстановление после кратковременной ошибки (FAIL #1)
                st.health_state = ServerHealthState.ONLINE
                st.consecutive_fails = 0
                st.consecutive_successes = 0
                st.next_check_at = None
                st.problem_started_at = None

            elif st.health_state == ServerHealthState.ONLINE:
                st.consecutive_fails = 0
                st.consecutive_successes += 1
                st.next_check_at = None
                st.problem_started_at = None

                if st.last_alert_sent_state == ServerHealthState.PROBLEM:
                    alerts_to_send.append({
                        "text": ALERT_SERVER_RESTORED.format(
                            server_name=safe(server.name),
                            server_id=server.id,
                        ),
                        "target_alert_state": ServerHealthState.ONLINE,
                    })

                # Проверка диска (с 1-часовым кулдауном) для Xray узлов
                disk_percent = None
                if is_xray_node and xray_data and isinstance(xray_data, dict):
                    try:
                        sys_load = xray_data.get("system_load")
                        if isinstance(sys_load, dict) and sys_load.get("disk_percent") is not None:
                            disk_percent = float(sys_load["disk_percent"])
                        else:
                            disk_usage = xray_data.get("disk_usage", {})
                            if isinstance(disk_usage, dict):
                                raw_dp = disk_usage.get("used_percent")
                                if raw_dp is not None and isinstance(raw_dp, (int, float)):
                                    disk_percent = float(raw_dp)
                    except Exception as exc:
                        logger.warning("Error reading disk usage for Xray server %s: %s", server.id, exc)

                if disk_percent is not None:
                    if disk_percent > 85.0:
                        should_alert = False
                        if st.disk_alert_last_sent is None or (now_m - st.disk_alert_last_sent) >= DISK_ALERT_COOLDOWN_SECONDS:
                            should_alert = True

                        if should_alert:
                            alerts_to_send.append({
                                "text": ALERT_SERVER_DISK_CRITICAL.format(
                                    server_name=safe(server.name),
                                    server_id=server.id,
                                    disk_percent=disk_percent,
                                ),
                            })
                            st.disk_alert_last_sent = now_m
                    elif disk_percent <= 80.0:
                        st.disk_alert_last_sent = None

                # Проверка лимита хостинга (hosting_limit_gb)
                extra = dict(server.extra_data or {})
                usage = get_server_hosting_usage(extra)
                if usage.limit_gb is not None:
                    limit_gb = usage.limit_gb
                    current_cycle = usage.current_cycle
                    pct = usage.pct
                    used_gb = usage.host_gb
                    used_gb_fmt = f"{int(round(used_gb))}" if abs(used_gb - round(used_gb)) < 0.05 else f"{used_gb:.1f}"

                    alert_cycle = extra.get("hosting_alert_cycle")
                    alert_level = int(extra.get("hosting_alert_level", 0) or 0)
                    if alert_cycle != current_cycle:
                        alert_cycle = current_cycle
                        alert_level = 0

                    hosting_alert_to_send = None
                    new_alert_level = alert_level
                    should_disable = False

                    if pct >= 90.0 and alert_level < 90:
                        should_disable = True
                        new_alert_level = 90
                        hosting_alert_to_send = {
                            "text": ALERT_SERVER_HOSTING_TRAFFIC_CRITICAL.format(
                                server_name=safe(server.name),
                                used_gb=used_gb_fmt,
                                limit_gb=limit_gb,
                            ),
                        }
                    elif pct >= 80.0 and alert_level < 80:
                        new_alert_level = 80
                        hosting_alert_to_send = {
                            "text": ALERT_SERVER_HOSTING_TRAFFIC_WARN.format(
                                server_name=safe(server.name),
                                used_gb=used_gb_fmt,
                                limit_gb=limit_gb,
                            ),
                        }
                    elif pct < 80.0 and alert_level > 0:
                        new_alert_level = 0

                    # Decoupled immediate alert delivery: critical alerts must not be dropped when server is auto-disabled
                    if hosting_alert_to_send:
                        sent_ok = False
                        try:
                            sent_ok = await _send_admin_alert_msg(
                                bot,
                                hosting_alert_to_send["text"],
                                reply_markup=get_admin_node_keyboard(server.id),
                            )
                        except Exception as e:
                            logger.error("Failed to deliver hosting traffic alert for server %d: %s", server.id, e)

                        if not sent_ok:
                            # Delivery failed; retain current alert_level so next tick retries
                            new_alert_level = alert_level

                    if new_alert_level != alert_level or should_disable or alert_cycle != extra.get("hosting_alert_cycle"):
                        extra["hosting_alert_level"] = new_alert_level
                        extra["hosting_alert_cycle"] = current_cycle
                        server.extra_data = extra
                        if should_disable:
                            server.is_active = False
                            server.disabled_reason = "HOSTING_TRAFFIC_LIMIT"
                            st.health_state = ServerHealthState.MANUAL_DISABLED
                            st.consecutive_fails = 0
                            st.consecutive_successes = 0
                            st.problem_started_at = None
                            st.next_check_at = None
                            st.last_alert_sent_state = None

                        async with session_scope() as session:
                            fresh = await get_server_by_id(session, server.id, for_update=True)
                            if fresh:
                                fresh_extra = dict(fresh.extra_data or {})
                                fresh_extra["hosting_alert_level"] = new_alert_level
                                fresh_extra["hosting_alert_cycle"] = current_cycle
                                if should_disable:
                                    fresh.is_active = False
                                    await update_server(
                                        session,
                                        fresh,
                                        is_active=False,
                                        disabled_reason="HOSTING_TRAFFIC_LIMIT",
                                        disabled_at=now_utc(),
                                        health_state=ServerHealthState.MANUAL_DISABLED,
                                        consecutive_fails=0,
                                        consecutive_successes=0,
                                        problem_started_at=None,
                                        next_check_at=None,
                                        last_alert_sent_state=None,
                                        extra_data=fresh_extra,
                                    )
                                    hosting_auto_disabled = True
                                    topology_dirty = True
                                else:
                                    await update_server(session, fresh, extra_data=fresh_extra)

            elif st.health_state == ServerHealthState.PROBLEM:
                st.consecutive_fails = 0
                st.consecutive_successes += 1

                if st.consecutive_successes >= REQUIRED_STABLE_SUCCESSES:
                    st.health_state = ServerHealthState.ONLINE
                    st.problem_started_at = None
                    st.next_check_at = None
                    st.last_core_reminder_at = None

                    alerts_to_send.append({
                        "text": ALERT_SERVER_RESTORED.format(
                            server_name=safe(server.name),
                            server_id=server.id,
                        ),
                        "target_alert_state": ServerHealthState.ONLINE,
                    })

        else:
            # Ошибка проверки (FAIL)
            st.consecutive_successes = 0

            if st.health_state == ServerHealthState.ONLINE:
                # Переход в WAITING_CONFIRMATION, планирование проверки через 60 секунд БЕЗ СЛИПА!
                st.health_state = ServerHealthState.WAITING_CONFIRMATION
                st.consecutive_fails = 1
                st.next_check_at = now_m + CONFIRMATION_DELAY_SECONDS

            elif st.health_state == ServerHealthState.WAITING_CONFIRMATION:
                # 60 секунд прошли, FAIL #2 подтвержден -> Переход в PROBLEM!
                st.health_state = ServerHealthState.PROBLEM
                st.consecutive_fails = 2
                st.problem_started_at = now_m
                st.next_check_at = None

                alerts_to_send.append({
                    "text": ALERT_SERVER_PROBLEM.format(
                        server_name=safe(server.name),
                        server_id=server.id,
                    ),
                    "target_alert_state": ServerHealthState.PROBLEM,
                })

            elif st.health_state == ServerHealthState.PROBLEM:
                st.consecutive_fails += 1
                st.next_check_at = None

        # Повторная попытка отправки не доставленных алертов или напоминаний о сбое
        if not alerts_to_send:
            if st.health_state == ServerHealthState.PROBLEM:
                if st.last_alert_sent_state != ServerHealthState.PROBLEM:
                    alerts_to_send.append({
                        "text": ALERT_SERVER_PROBLEM.format(
                            server_name=safe(server.name),
                            server_id=server.id,
                        ),
                        "target_alert_state": ServerHealthState.PROBLEM,
                    })
                else:
                    last_rem = st.last_core_reminder_at
                    if last_rem is None:
                        st.last_core_reminder_at = now_m
                    elif (now_m - last_rem) >= OUTAGE_REMINDER_INTERVAL_SECONDS:
                        outage_dur = (now_m - st.problem_started_at) if st.problem_started_at else (now_m - last_rem)
                        alerts_to_send.append({
                            "text": ALERT_SERVER_OUTAGE_REMINDER.format(
                                server_name=safe(server.name),
                                server_id=server.id,
                                hours_text=_format_outage_duration(outage_dur),
                            ),
                            "is_reminder": True,
                            "reminder_ts": now_m,
                        })

        # 5. ЕДИНСТВЕННОЕ АТОМАРНОЕ ОБНОВЛЕНИЕ БД НА КАЖДУЮ ПРОВЕРКУ (СНАЧАЛА CAS UPDATE)
        update_kwargs = {
            "health_state": st.health_state,
            "consecutive_fails": st.consecutive_fails,
            "consecutive_successes": st.consecutive_successes,
            "last_alert_sent_state": st.last_alert_sent_state,
        }

        if is_xray_node and isinstance(xray_data, dict) and xray_data:
            extra_update = _build_v2_extra_patch(server.extra_data, None, xray_data)
            if extra_update:
                update_kwargs["extra_data"] = extra_update

        if is_healthy:
            update_kwargs["last_successful_check"] = now_utc()
            if is_xray_node and xray_data:
                if xray_data.get("role") or xray_data.get("capabilities"):
                    derived_caps = derive_v2_capabilities(None, xray_data)
                    if derived_caps and sorted(derived_caps) != sorted(server.capabilities or []):
                        update_kwargs["capabilities"] = derived_caps

            if st.health_state == ServerHealthState.ONLINE:
                update_kwargs["problem_started_at"] = None
                update_kwargs["next_check_at"] = None

        if st.health_state == ServerHealthState.WAITING_CONFIRMATION and st.next_check_at:
            rem = st.next_check_at - now_m
            next_dt = datetime.fromtimestamp(time.time() + max(0.0, rem), tz=timezone.utc)
            update_kwargs["next_check_at"] = next_dt

        if st.health_state == ServerHealthState.PROBLEM and st.consecutive_fails == 2:
            update_kwargs["problem_started_at"] = now_utc()
            update_kwargs["next_check_at"] = None

        async with session_scope() as session:
            # Monotonic CAS update for Xray generation
            if is_healthy and is_xray_node and xray_epoch:
                prev_epoch = server.xray_instance_epoch
                boot_id = xray_data.get("boot_id") if xray_data else None
                starttime = xray_data.get("starttime") if xray_data else None
                cas_ok, updated_srv = await update_server_xray_epoch_cas(
                    session,
                    server.id,
                    expected_boot_id=server.xray_instance_boot_id,
                    expected_starttime=server.xray_instance_starttime,
                    new_epoch=xray_epoch,
                    new_boot_id=boot_id,
                    new_starttime=starttime,
                )
                if cas_ok and updated_srv:
                    server.xray_instance_epoch = updated_srv.xray_instance_epoch
                    server.xray_instance_boot_id = updated_srv.xray_instance_boot_id
                    server.xray_instance_starttime = updated_srv.xray_instance_starttime

                    # Синхронизация динамических метаданных и health-полей при изменениях на ноде
                    if xray_data and isinstance(xray_data, dict):
                        extra_patch = _build_v2_extra_patch(
                            updated_srv.extra_data, None, xray_data
                        )
                        extra = dict(updated_srv.extra_data or {})
                        extra_changed = bool(extra_patch)
                        if extra_patch:
                            for pk, pv in extra_patch.items():
                                if pv is None and pk in ("last_apply", "ssl_cert"):
                                    extra.pop(pk, None)
                                else:
                                    extra[pk] = pv
                        cdn_domains = xray_data.get("cdn_domains")
                        if isinstance(cdn_domains, dict) and not cdn_domains.get("cdn_turboflare") and "turboflare_domain" in extra:
                            extra.pop("turboflare_domain", None)
                            extra_changed = True
                        if extra_changed:
                            updated_srv.extra_data = extra
                            flag_modified(updated_srv, "extra_data")
                            server.extra_data = extra
                            await session.flush()
                        update_kwargs.pop("extra_data", None)

                    # Trigger VLESS re-provisioning only if epoch changed or is unsynced on a VLESS sync target server (ru_hub)
                    if is_vless_sync_target_server(updated_srv):
                        cur_synced = (dict(updated_srv.extra_data or {})).get("vless_synced_epoch")
                        if prev_epoch != updated_srv.xray_instance_epoch or cur_synced != updated_srv.xray_instance_epoch:
                            from services.vless_subscription_service import VlessSubscriptionService
                            VlessSubscriptionService.sync_all_active_to_server_background(updated_srv.id)

            applied = False
            if not hosting_auto_disabled:
                db_server, applied = await update_server_health_snapshot(
                    session,
                    server.id,
                    expected_health_state=expected_health_state,
                    expected_consecutive_fails=expected_consecutive_fails,
                    expected_consecutive_successes=expected_consecutive_successes,
                    new_health_state=st.health_state,
                    **update_kwargs,
                )
                if applied and db_server is not None:
                    server.health_state = db_server.health_state
                    server.is_active = db_server.is_active
                if applied and expected_health_state != st.health_state:
                    topology_states = (ServerHealthState.PROBLEM, ServerHealthState.MANUAL_DISABLED)
                    if (
                        expected_health_state in topology_states
                        or st.health_state in topology_states
                        or (
                            expected_health_state == ServerHealthState.WAITING_CONFIRMATION
                            and st.health_state == ServerHealthState.ONLINE
                        )
                    ):
                        topology_dirty = True
                if db_server and not applied:
                    st.sync_from_db_server(db_server)

        # 6. ВНЕШНИЕ ПОБОЧНЫЕ ЭФФЕКТЫ (TELEGRAM ALERT) ВЫПОЛНЯЮТСЯ СТРОГО ПОСЛЕ УСПЕШНОГО CAS
        if applied and alerts_to_send:
            for alert_to_send in alerts_to_send:
                target_state = alert_to_send.get("target_alert_state")
                reminder_ts = alert_to_send.get("reminder_ts")
                sent_ok = False
                try:
                    sent_ok = await _send_admin_alert_msg(
                        bot,
                        alert_to_send["text"],
                        reply_markup=get_admin_node_keyboard(server.id),
                    )
                except Exception as e:
                    logger.error("Failed to deliver node monitor alert: %s", e)
                    sent_ok = False

                if sent_ok:
                    if reminder_ts is not None:
                        st.last_core_reminder_at = reminder_ts
                    if target_state:
                        st.last_alert_sent_state = target_state

                        async with session_scope() as session:
                            fresh_server = await get_server_by_id(session, server.id)
                            if fresh_server and fresh_server.health_state != ServerHealthState.MANUAL_DISABLED:
                                await update_server(
                                    session,
                                    fresh_server,
                                    last_alert_sent_state=st.last_alert_sent_state,
                                )
                else:
                    logger.warning(
                        "Failed to deliver core node monitor alert for server %d; state flag not advanced.",
                        server.id,
                    )

        # Telemetry alerts: last_apply failures & TLS cert expiry (<= 7 days)
        if is_healthy and is_xray_node and isinstance(server.extra_data, dict):
            tel_extra = dict(server.extra_data or {})
            tel_extra_patch: dict[str, Any] = {}
            tel_alerts_to_send: list[tuple[str, str, Any]] = []

            last_apply = tel_extra.get("last_apply")
            if isinstance(last_apply, dict):
                apply_status_raw = str(last_apply.get("status") or "").strip().lower()
                apply_failed = (
                    last_apply.get("ok") is False
                    or apply_status_raw in ("failed", "error", "rollback")
                    or bool(
                        last_apply.get("rollback_error")
                        or last_apply.get("error")
                        or last_apply.get("rolled_back")
                    )
                )
                if apply_failed:
                    err_text = str(
                        last_apply.get("rollback_error")
                        or last_apply.get("error")
                        or last_apply.get("status")
                        or "rollback"
                    ).strip()[:300]
                    applied_at_raw = str(
                        last_apply.get("applied_at")
                        or last_apply.get("timestamp")
                        or last_apply.get("ts")
                        or last_apply.get("at")
                        or ""
                    ).strip()
                    apply_key = applied_at_raw or f"{apply_status_raw}:{err_text[:60]}"
                    if str(tel_extra.get("last_apply_alert_ts") or "").strip() != apply_key:
                        tel_alerts_to_send.append((
                            "last_apply_alert_ts",
                            ALERT_SERVER_LAST_APPLY_FAILED.format(
                                server_name=safe(server.name),
                                server_id=server.id,
                                applied_at=safe(applied_at_raw or "неизвестно"),
                                error_text=safe(err_text),
                            ),
                            apply_key,
                        ))
                elif "last_apply_alert_ts" in tel_extra:
                    tel_extra_patch["last_apply_alert_ts"] = None
            elif "last_apply_alert_ts" in tel_extra:
                tel_extra_patch["last_apply_alert_ts"] = None

            ssl_cert = tel_extra.get("ssl_cert")
            if isinstance(ssl_cert, dict):
                raw_days = ssl_cert.get("days_left")
                if isinstance(raw_days, (int, float)) and not isinstance(raw_days, bool):
                    days_left = int(raw_days)
                    now_wall = time.time()
                    if days_left <= 7:
                        last_ssl_sent = tel_extra.get("ssl_alert_last_sent_at")
                        if not isinstance(last_ssl_sent, (int, float)) or (now_wall - float(last_ssl_sent)) >= 86400.0:
                            exp_str = str(ssl_cert.get("expires_at") or "—").strip()
                            tel_alerts_to_send.append((
                                "ssl_alert_last_sent_at",
                                ALERT_SERVER_SSL_CERT_EXPIRING.format(
                                    server_name=safe(server.name),
                                    server_id=server.id,
                                    days_left=days_left,
                                    expires_at=safe(exp_str),
                                ),
                                now_wall,
                            ))
                    elif days_left > 14 and "ssl_alert_last_sent_at" in tel_extra:
                        tel_extra_patch["ssl_alert_last_sent_at"] = None

            for meta_key, alert_text, meta_val in tel_alerts_to_send:
                try:
                    sent_ok = await _send_admin_alert_msg(
                        bot,
                        alert_text,
                        reply_markup=get_admin_node_keyboard(server.id),
                    )
                    if sent_ok:
                        tel_extra_patch[meta_key] = meta_val
                except Exception as exc:
                    logger.error(
                        "Failed to deliver telemetry alert (%s) for server %d: %s",
                        meta_key,
                        server.id,
                        exc,
                    )

            if tel_extra_patch:
                try:
                    async with session_scope() as session:
                        fresh_tel = await get_server_by_id(session, server.id, for_update=True)
                        if fresh_tel is not None:
                            fresh_extra = dict(fresh_tel.extra_data or {})
                            for k, v in tel_extra_patch.items():
                                if v is None:
                                    fresh_extra.pop(k, None)
                                else:
                                    fresh_extra[k] = v
                            await update_server(session, fresh_tel, extra_data=fresh_extra)
                            server.extra_data = fresh_extra
                except Exception as exc:
                    logger.warning("Failed to persist telemetry alert state for server %d: %s", server.id, exc)

    # Bounded parallel fan-out: evaluate non-ru_hub nodes (including eu_hubs) first so
    # ru_hub bridge health evaluation always sees settled eu_hub states in the same cycle.
    non_ru_servers = [s for s in servers if get_server_role(s) != "ru_hub"]
    ru_servers = [s for s in servers if get_server_role(s) == "ru_hub"]
    try:
        for group in (non_ru_servers, ru_servers):
            for batch_start in range(0, len(group), CHECK_PARALLELISM):
                batch = group[batch_start:batch_start + CHECK_PARALLELISM]
                batch_results = await asyncio.gather(*(_check_one(s) for s in batch), return_exceptions=True)
                for res in batch_results:
                    if isinstance(res, Exception):
                        logger.error("Error in node_monitor batch check: %s", res)
    finally:
        await shared_probe_sess.close()

    if topology_dirty or is_topology_retry_pending():
        await _run_topology_sync_with_retry_tracking("monitor_cycle")

    try:
        from utils.http_rate_limiter import refresh_dynamic_trusted_proxies

        await refresh_dynamic_trusted_proxies(servers)
    except Exception as proxy_sync_exc:
        logger.debug("Dynamic trusted proxies refresh skipped in monitor cycle: %s", proxy_sync_exc)


async def node_monitor_loop(bot: Bot, shutdown_event: asyncio.Event):
    from services.topology_sync_service import is_topology_retry_pending

    logger.info("Starting node_monitor_loop background worker...")
    await _run_topology_sync_with_retry_tracking("startup")
    if not is_topology_retry_pending():
        logger.info("Initial v2 topology sync to all nodes completed on worker startup.")

    while not shutdown_event.is_set():
        try:
            await check_node_resources_and_alerts(bot)
        except Exception as exc:
            logger.error("Error in node_monitor_loop: %s", exc, exc_info=True)

        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=CHECK_INTERVAL_SECONDS)
            break
        except asyncio.TimeoutError:
            pass
    logger.info("Stopped node_monitor_loop background worker.")
