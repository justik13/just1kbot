import asyncio
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)

PRODUCTION_HEARTBEAT_FILE = Path("/run/just1kbot/heartbeat")


def get_heartbeat_file() -> Path:
    """Return the production runtime path unless explicitly overridden."""
    explicit_file = os.environ.get("JUST1KBOT_HEARTBEAT_FILE")
    if explicit_file:
        return Path(explicit_file)
    return PRODUCTION_HEARTBEAT_FILE


HEARTBEAT_FILE = get_heartbeat_file()
HEARTBEAT_INTERVAL = 60.0


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
        except asyncio.CancelledError:
            logger.info("Heartbeat worker cancelled")
            break
        except Exception as e:
            logger.error(f"Heartbeat worker error: {e}", exc_info=True)
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=heartbeat_interval)
                break
            except asyncio.TimeoutError:
                pass

    if health_check():
        _write_heartbeat(final=True)
    logger.info("Heartbeat worker stopped gracefully")


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
