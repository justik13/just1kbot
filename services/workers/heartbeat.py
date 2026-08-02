from bot import texts
import asyncio
import logging
import os
import time
from pathlib import Path

from cachetools import TTLCache

from services.amnezia_client import _circuit_breakers
from config.settings import get_settings
from utils.logging_security import safe_url_target

logger = logging.getLogger("BackgroundWorker")

PRODUCTION_HEARTBEAT_FILE = Path("/run/just1kbot/heartbeat")


def get_heartbeat_file() -> Path:
    """Return the production runtime path unless explicitly overridden."""
    explicit_file = os.environ.get("JUST1KBOT_HEARTBEAT_FILE")
    if explicit_file:
        return Path(explicit_file)
    project_dir = os.environ.get("JUST1KBOT_DIR") or os.environ.get("just1kbot_DIR")
    if project_dir:
        return Path(project_dir) / ".heartbeat"
    return PRODUCTION_HEARTBEAT_FILE


HEARTBEAT_FILE = get_heartbeat_file()
HEARTBEAT_INTERVAL = 60.0

# ИСПРАВЛЕНО: TTLCache вместо бесконечного dict.
_api_alert_sent: TTLCache[str, float] = TTLCache(maxsize=1000, ttl=7200)
_API_ALERT_COOLDOWN = 1800.0

_bot_ref = None


async def heartbeat_loop(
    shutdown_event: asyncio.Event,
    health_check=lambda: True,
    *,
    interval: float | None = None,
):
    heartbeat_interval = HEARTBEAT_INTERVAL if interval is None else interval
    logger.info(f"Heartbeat worker started, file={HEARTBEAT_FILE}")
    if health_check():
        _write_heartbeat()

    while not shutdown_event.is_set():
        try:
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=heartbeat_interval)
                break
            except asyncio.TimeoutError:
                pass
            if health_check():
                _write_heartbeat()
            await _check_circuit_breakers()
        except asyncio.CancelledError:
            logger.info("Heartbeat worker cancelled")
            break
        except Exception as e:
            logger.error(f"Heartbeat worker error: {e}", exc_info=True)
            if shutdown_event.is_set():
                break
            await asyncio.sleep(heartbeat_interval)

    if health_check():
        _write_heartbeat(final=True)
    logger.info("Heartbeat worker stopped gracefully")


async def _check_circuit_breakers():
    from database.connection import session_scope
    from database.repositories.servers_repo import get_server_by_api_url

    settings = get_settings()
    now = time.monotonic()

    for api_url, cb in list(_circuit_breakers.items()):
        if not cb.is_open:
            continue
        last_alert = _api_alert_sent.get(api_url, 0)
        if now - last_alert < _API_ALERT_COOLDOWN:
            continue

        safe_target = safe_url_target(api_url)
        server_name = safe_target
        try:
            async with session_scope() as session:
                server = await get_server_by_api_url(session, api_url)
                if server:
                    server_name = server.name
        except Exception:
            pass

        alert_msg = (
            texts.RUNTIME_SERVICES_WORKERS_HEARTBEAT_L99_1.format(value_0=server_name, value_1=safe_target, value_2=cb.recovery_timeout)
        )

        if _bot_ref is not None:
            for admin_id in settings.ADMIN_IDS:
                try:
                    await _bot_ref.send_message(admin_id, alert_msg, parse_mode="HTML")
                except Exception as e:
                    logger.warning("Failed to send CB alert to admin %s: %s", admin_id, e)
        else:
            logger.warning(
                "CircuitBreaker OPEN for server '%s' (%s). bot_ref is None.",
                server_name,
                safe_target,
            )

        _api_alert_sent[api_url] = now


def set_bot_ref(bot):
    global _bot_ref
    _bot_ref = bot


def get_bot_ref():
    return _bot_ref


def _write_heartbeat(final: bool = False):
    try:
        temp_file = HEARTBEAT_FILE.with_suffix(".tmp")
        if final:
            content = f"STOPPED {int(time.time())}\n"
        else:
            content = f"{int(time.time())}\n"
        with open(temp_file, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_file, HEARTBEAT_FILE)
        try:
            os.chmod(HEARTBEAT_FILE, 0o640)
        except PermissionError:
            pass
    except Exception as e:
        logger.warning(f"Failed to write heartbeat file: {e}")
