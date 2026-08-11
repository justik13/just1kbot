from bot import texts
import asyncio
import logging
import os
import time
from pathlib import Path

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
    return PRODUCTION_HEARTBEAT_FILE


HEARTBEAT_FILE = get_heartbeat_file()
HEARTBEAT_INTERVAL = 60.0

# Отслеживание серверов с активным алертом для исключения спама
_active_open_cb_alerts: set[str] = set()
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
    for api_url, cb in list(_circuit_breakers.items()):
        safe_target = safe_url_target(api_url)

        if cb.is_open:
            if api_url in _active_open_cb_alerts:
                continue

            logger.warning(
                "CircuitBreaker OPEN for server endpoint '%s' (recovery_timeout=%.0fs).",
                safe_target,
                cb.recovery_timeout,
            )
            _active_open_cb_alerts.add(api_url)
        else:
            if api_url in _active_open_cb_alerts:
                _active_open_cb_alerts.remove(api_url)
                logger.info("CircuitBreaker CLOSED / recovered for server endpoint '%s'", safe_target)


def set_bot_ref(bot):
    global _bot_ref

    _bot_ref = bot


def get_bot_ref():
    return _bot_ref


def _write_heartbeat(final: bool = False):
    try:
        temp_file = HEARTBEAT_FILE.with_suffix(".tmp")
        os.makedirs(HEARTBEAT_FILE.parent, exist_ok=True)
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
