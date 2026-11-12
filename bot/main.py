import asyncio
import html
import logging
import os
import signal
import traceback
import uuid

import aiofiles.os
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.base import BaseStorage
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ErrorEvent
from aiohttp import web
from aiohttp.web_log import AccessLogger
from cachetools import TTLCache
from cryptography.fernet import Fernet

from bot import texts
from bot.handlers.notifications import router as notifications_router
from bot.handlers.webhook import setup_webhook_routes
from bot.keyboards.notifications import get_dismiss_alert_keyboard
from bot.middlewares import (
    CorrelationFilter,
    CorrelationMiddleware,
    correlation_scope,
)
from config.constants import WHITE_INTERNET_SUB_PATH_PREFIX
from config.settings import get_settings
from database.connection import close_db, init_db
from services.workers import (
    shutdown_event,
    start_background_workers,
    stop_background_workers,
)
from services.yookassa_service import close_yookassa_client
from utils.http_rate_limiter import HttpRateLimiter, get_trusted_client_ip
from utils.logging_security import (
    install_sensitive_data_filter,
    sanitize_short,
    sanitize_text,
)
from utils.telegram import safe_callback_answer, safe_send_message


def _resolve_log_level() -> str:
    """LOG_LEVEL from env first, then Settings (picks up .env), then INFO.

    Kept exception-safe: bot.main is imported by tooling/tests whose
    environment may lack required Settings fields.
    """
    allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
    raw = os.getenv("LOG_LEVEL")
    if raw:
        normalized = raw.strip().upper()
        if normalized in allowed:
            return normalized
    try:
        level = get_settings().LOG_LEVEL.strip().upper()
        if level in allowed:
            return level
    except Exception:
        pass
    return "INFO"


logging.basicConfig(
    level=_resolve_log_level(),
    format=(
        "%(asctime)s - %(levelname)s - "
        "[%(request_id)s] %(name)s: %(message)s"
    ),
    datefmt="%Y-%m-%d %H:%M:%S",
)

root_logger = logging.getLogger()
install_sensitive_data_filter(root_logger)
root_logger.addFilter(CorrelationFilter())
for handler in root_logger.handlers:
    handler.addFilter(CorrelationFilter())

logger = logging.getLogger(__name__)

_error_alert_cache: TTLCache[str, bool] = TTLCache(
    maxsize=10000, ttl=300.0
)


def _is_ci_test_mode() -> bool:
    """Return True only for the explicit offline CI smoke-test mode."""
    return os.getenv("CI_TEST_MODE", "false").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


async def global_error_handler(
    event: ErrorEvent, **kwargs
) -> bool:
    from bot.middlewares.correlation import get_current_request_id

    request_id = get_current_request_id()
    exception = event.exception
    error_type = type(exception).__name__

    try:
        tb_lines = traceback.format_exception(
            type(exception), exception, exception.__traceback__
        )
        tb_text = "".join(tb_lines)
        tb_sanitized = sanitize_text(tb_text)

        if len(tb_sanitized) > 4000:
            tb_sanitized = (
                tb_sanitized[:1000]
                + "\n...[middle frames truncated]...\n"
                + tb_sanitized[-3000:]
            )

        logger.critical(
            "[%s] Unhandled exception: %s\n%s",
            request_id,
            error_type,
            tb_sanitized,
        )
    except Exception:
        logger.critical(
            "[%s] Unhandled exception: %s", request_id, error_type
        )

    try:
        settings = get_settings()
        error_type_safe = html.escape(error_type)
        error_short = html.escape(
            sanitize_short(str(exception), 200)
        )

        error_msg = texts.ALERT_CRITICAL_BOT_ERROR.format(
            request_id=request_id,
            error_type=error_type_safe,
            error_short=error_short,
        )

        alert_key = f"{error_type_safe}:{error_short}"

        if alert_key not in _error_alert_cache:
            _error_alert_cache[alert_key] = True

            for admin_id in settings.ADMIN_IDS:
                try:
                    await safe_send_message(
                        event.bot,
                        admin_id,
                        error_msg,
                        reply_markup=get_dismiss_alert_keyboard("general"),
                    )
                except Exception as exc:
                    logger.error(
                        "[%s] Failed to deliver error alert to admin %s: %s",
                        request_id,
                        admin_id,
                        exc,
                    )
    except Exception as e:
        logger.error("[%s] Failed to send error alert: %s", request_id, e)

    try:
        if event.update.callback_query:
            await safe_callback_answer(
                event.update.callback_query,
                texts.ERROR_TECHNICAL_ALERT,
                show_alert=True,
            )
    except Exception:
        pass

    return True


async def setup_bot(
    bot: Bot | None = None,
    storage: BaseStorage | None = None,
) -> tuple[Bot, Dispatcher]:
    settings = get_settings()

    if bot is None:
        bot = Bot(token=settings.BOT_TOKEN)
    if storage is None:
        storage = MemoryStorage()

    dp = Dispatcher(storage=storage)

    dp.message.middleware(CorrelationMiddleware())
    dp.callback_query.middleware(CorrelationMiddleware())

    notifications_router._parent_router = None
    dp.include_router(notifications_router)
    dp.errors.register(global_error_handler)

    return bot, dp


class HealthcheckAccessLogger(AccessLogger):
    """Suppresses routine 200 OK access logs (healthchecks, synthetic pings, subscription polls) and masks tokens."""

    def log(self, request: web.Request, response: web.StreamResponse, time: float) -> None:
        # 1. Suppress routine 200 OK healthchecks and synthetic monitor pings
        if response.status == 200 and (request.path == "/health" or request.path.endswith("/ping")):
            return

        client_ip = (
            request.headers.get("X-Real-IP")
            or request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
            or request.remote
        )
        user_agent = request.headers.get("User-Agent", "-")

        # 2. Synthetic ping failure logging (not a secret token)
        if request.path.endswith("/ping"):
            self.logger.warning(
                '%s "%s %s %s" %s %s [UA: %s]',
                client_ip,
                request.method,
                request.path,
                f"HTTP/{request.version.major}.{request.version.minor}",
                response.status,
                response.body_length,
                user_agent,
            )
            return

        sub_prefix = (
            os.getenv("WHITE_INTERNET_SUB_PATH_PREFIX") or WHITE_INTERNET_SUB_PATH_PREFIX
        ).strip().rstrip("/")
        if not sub_prefix.startswith("/"):
            sub_prefix = f"/{sub_prefix}"

        matched_prefix = None
        for p in (f"{sub_prefix}/", "/sub/wl/", "/sub/"):
            if request.path.startswith(p):
                matched_prefix = p
                break

        if matched_prefix:
            masked_path = f"{matched_prefix}***"
            # Routine 200 OK subscription polls are logged at DEBUG to prevent production log spam
            if response.status == 200:
                self.logger.debug(
                    '%s "%s %s %s" %s %s [UA: %s]',
                    client_ip,
                    request.method,
                    masked_path,
                    f"HTTP/{request.version.major}.{request.version.minor}",
                    response.status,
                    response.body_length,
                    user_agent,
                )
                return

            # Failed requests (4xx, 5xx) are logged at WARNING with real client IP and User-Agent
            self.logger.warning(
                '%s "%s %s %s" %s %s [UA: %s]',
                client_ip,
                request.method,
                masked_path,
                f"HTTP/{request.version.major}.{request.version.minor}",
                response.status,
                response.body_length,
                user_agent,
            )
            return

        super().log(request, response, time)


@web.middleware
async def _http_correlation_middleware(request: web.Request, handler):
    """Give every public HTTP request the same request_id as Telegram updates."""
    with correlation_scope(uuid.uuid4().hex[:8]):
        return await handler(request)


_http_limiter = HttpRateLimiter()


@web.middleware
async def _http_rate_limit_middleware(request: web.Request, handler):
    """Process-local rate limiting for incoming HTTP webhooks and endpoints."""
    if request.path in {"/health", "/webhook/yookassa", "/yookassa/webhook"}:
        return await handler(request)
    client_ip = get_trusted_client_ip(request)
    is_allowed, retry_after = _http_limiter.check(client_ip)
    if not is_allowed:
        return web.Response(
            status=429,
            text="Too Many Requests",
            headers={"Retry-After": str(retry_after)},
        )
    return await handler(request)


async def start_webhook_server(port: int, bot: object | None = None):
    app = web.Application(client_max_size=64 * 1024)
    app["trusted_proxies"] = get_settings().TRUSTED_PROXIES
    if bot is not None:
        app["bot"] = bot
    app.middlewares.append(_http_correlation_middleware)
    app.middlewares.append(_http_rate_limit_middleware)
    setup_webhook_routes(app)

    runner = web.AppRunner(app, access_log_class=HealthcheckAccessLogger)
    await runner.setup()

    host = os.getenv("WEBHOOK_HOST", "127.0.0.1")
    site = web.TCPSite(runner, host, port)
    await site.start()

    logger.info("Webhook server started on %s:%d", host, port)
    return runner


async def main():
    settings = None
    bot = None
    dp = None
    webhook_runner = None

    try:
        settings = get_settings()
        ci_test_mode = _is_ci_test_mode()
        shutdown_event.clear()

        config_dir = os.getenv("JUST1KBOT_CONFIG_DIR", "/etc/just1kbot")
        from pathlib import Path
        backup_key_path = Path(config_dir) / "backup.agekey"

        is_docker = os.getenv("DOCKER_DEPLOYMENT", "false").lower() == "true"

        if settings.DB_ENCRYPTION_KEY and not is_docker and not await aiofiles.os.path.exists(
            str(backup_key_path)
        ):
            logger.critical(
                "CRITICAL WARNING: DB_ENCRYPTION_KEY is present but %s is missing!",
                backup_key_path,
            )
            raise RuntimeError(f"Startup aborted: {backup_key_path} is missing. Backups would be irrecoverable.")

        try:
            Fernet(settings.DB_ENCRYPTION_KEY.encode("utf-8"))
        except Exception as exc:
            raise RuntimeError(
                "DB_ENCRYPTION_KEY is not a valid Fernet key"
            ) from exc

        logger.info("Инициализация БД...")
        await init_db()

        bot, dp = await setup_bot()

        webhook_runner = await start_webhook_server(
            settings.YOOKASSA_WEBHOOK_PORT,
            bot=bot,
        )

        if ci_test_mode:
            logger.info(
                "CI_TEST_MODE enabled; skipping Telegram polling and background workers"
            )

        loop = asyncio.get_running_loop()

        def _signal_handler():
            logger.info("Received shutdown signal (SIGTERM/SIGINT)")
            shutdown_event.set()

        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, _signal_handler)
            except NotImplementedError:
                pass

        if ci_test_mode:
            logger.info(
                "CI_TEST_MODE ready: HTTP health endpoint is available; "
                "waiting for test container shutdown"
            )
            await shutdown_event.wait()
            return

        await start_background_workers(bot)

        logger.info("Запуск polling...")
        polling_task = asyncio.create_task(dp.start_polling(bot, handle_signals=False))
        shutdown_task = asyncio.create_task(shutdown_event.wait())

        done, pending = await asyncio.wait(
            [polling_task, shutdown_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        if shutdown_event.is_set():
            logger.info("Shutdown requested, stopping polling...")
            await dp.stop_polling()
            polling_task.cancel()
            try:
                await polling_task
            except asyncio.CancelledError:
                pass
        else:
            if polling_task.cancelled():
                raise RuntimeError("Telegram polling was cancelled unexpectedly")
            polling_error = polling_task.exception()
            if polling_error is not None:
                raise polling_error
            raise RuntimeError("Telegram polling stopped unexpectedly")

        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

    except Exception as e:
        logger.critical("Fatal error in main: %s", e, exc_info=True)
        raise

    finally:
        logger.info("Stopping background workers...")
        try:
            await stop_background_workers()
        except Exception as e:
            logger.error("Error stopping workers: %s", e)

        logger.info("Cleaning up resources...")

        if webhook_runner is not None:
            try:
                await webhook_runner.cleanup()
            except Exception as e:
                logger.error("Failed to clean up webhook server: %s", e)

        if dp is not None:
            try:
                await dp.storage.close()
            except Exception as e:
                logger.error("Failed to close dispatcher storage: %s", e)

        if bot is not None:
            try:
                await bot.session.close()
            except Exception as e:
                logger.error("Failed to close Telegram bot session: %s", e)

        try:
            await close_yookassa_client()
        except Exception as e:
            logger.error("Failed to close YooKassa client: %s", e)

        try:
            await close_db()
        except Exception as e:
            logger.error("Failed to close database: %s", e)

        logger.info("Работа бота завершена")


if __name__ == "__main__":
    asyncio.run(main())
