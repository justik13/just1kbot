import asyncio
import html
import logging
import os
import uuid
import signal
import traceback
from datetime import timedelta

import aiofiles.os
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.base import BaseStorage
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import (
    BotCommand,
    BotCommandScopeDefault,
    ErrorEvent,
    MenuButtonCommands,
)
from aiogram.utils.chat_action import ChatActionMiddleware
from aiohttp import web
from aiohttp.web_log import AccessLogger
from cachetools import TTLCache
from cryptography.fernet import Fernet

from bot import texts
from bot.handlers.admin.broadcast import (
    _background_tasks,
    _broadcast_stop_events,
    resume_pending_broadcasts,
)
from bot.handlers.webhook import setup_webhook_routes
from bot.middlewares import (
    ActionLockMiddleware,
    CleanChatMiddleware,
    CorrelationFilter,
    CorrelationMiddleware,
    DBSessionMiddleware,
    PrivateChatMiddleware,
    ThrottlingMiddleware,
    UserContextMiddleware,
    set_request_id,
)
from bot.middlewares.ban_check import BanCheckMiddleware
from bot.middlewares.clean_chat import stop_clean_chat_worker
from config.settings import get_settings
from database.connection import close_db, init_db
from services.amnezia_client import close_http_session
from utils.http_rate_limiter import HttpRateLimiter, get_trusted_client_ip
from services.workers import (
    shutdown_event,
    start_background_workers,
    stop_background_workers,
)
from services.yookassa_service import close_yookassa_client
from utils.logging_security import (
    install_sensitive_data_filter,
    sanitize_short,
    sanitize_text,
)

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
            tb_sanitized = tb_sanitized[:4000] + "\n...[truncated]"

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

    state = kwargs.get("state")
    if state:
        try:
            await state.clear()
        except Exception:
            pass

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
                    await event.bot.send_message(
                        admin_id, error_msg, parse_mode="HTML"
                    )
                except Exception:
                    pass
    except Exception as e:
        logger.error("[%s] Failed to send error alert: %s", request_id, e)

    try:
        if event.update.callback_query:
            await event.update.callback_query.answer(
                texts.ERROR_TECHNICAL_ALERT, show_alert=True
            )
        elif event.update.message:
            await event.update.message.answer(
                texts.ERROR_TECHNICAL_MESSAGE, parse_mode="HTML"
            )
    except Exception:
        pass

    return True


async def setup_bot_commands(bot: Bot):
    if _is_ci_test_mode():
        logger.info(
            "CI_TEST_MODE enabled; skipping Telegram API command registration"
        )
        return

    commands = [
        BotCommand(command="start", description=texts.BOT_START_DESCRIPTION),
    ]
    await bot.set_my_commands(
        commands, scope=BotCommandScopeDefault()
    )
    await bot.set_chat_menu_button(menu_button=MenuButtonCommands())


async def setup_bot(bot: Bot | None = None, storage: BaseStorage | None = None) -> tuple[Bot, Dispatcher]:
    settings = get_settings()

    if bot is None:
        bot = Bot(token=settings.BOT_TOKEN)
    if storage is None:
        # Bounded FSM lifetime + bounded I/O: without state_ttl/data_ttl Redis
        # keys live forever (OOM under noeviction), and without socket
        # timeouts a hung Redis stalls every FSM operation indefinitely.
        storage = RedisStorage.from_url(
            settings.REDIS_URL,
            state_ttl=timedelta(hours=24),
            data_ttl=timedelta(hours=24),
            connection_kwargs={
                "socket_timeout": 5.0,
                "socket_connect_timeout": 5.0,
                "health_check_interval": 30,
            },
        )
    dp = Dispatcher(storage=storage)

    dp.message.middleware(CorrelationMiddleware())
    dp.callback_query.middleware(CorrelationMiddleware())

    dp.message.middleware(PrivateChatMiddleware())
    dp.callback_query.middleware(PrivateChatMiddleware())

    dp.message.middleware(ThrottlingMiddleware())
    dp.callback_query.middleware(ThrottlingMiddleware())

    dp.message.middleware(DBSessionMiddleware())
    dp.callback_query.middleware(DBSessionMiddleware())

    dp.message.middleware(CleanChatMiddleware())

    dp.message.middleware(UserContextMiddleware())
    dp.callback_query.middleware(UserContextMiddleware())

    dp.message.middleware(BanCheckMiddleware())
    dp.callback_query.middleware(BanCheckMiddleware())

    dp.callback_query.middleware(ActionLockMiddleware())

    dp.message.middleware(ChatActionMiddleware())

    from bot.handlers.admin import admin_router
    from bot.handlers.connection import router as connection_router
    from bot.handlers.fallback import router as fallback_router
    from bot.handlers.payment import router as payment_router
    from bot.handlers.profile import router as profile_router
    from bot.handlers.start import router as start_router
    from bot.handlers.support import router as support_router
    from bot.handlers.white_internet import router as white_internet_router
    from integrations import get_all_bot_routers

    # Clean parent router state on all known module-level routers (core & integrations)
    # so setup_bot is strictly idempotent across multiple invocations and dynamic state transitions.
    for r in (
        start_router,
        profile_router,
        connection_router,
        white_internet_router,
        support_router,
        payment_router,
        admin_router,
        fallback_router,
    ):
        r._parent_router = None

    integration_routers = get_all_bot_routers()

    for r in [
        start_router,
        profile_router,
        connection_router,
        white_internet_router,
        *integration_routers,
        support_router,
        payment_router,
        admin_router,
        fallback_router,
    ]:
        dp.include_router(r)


    dp.errors.register(global_error_handler)

    await setup_bot_commands(bot)

    return bot, dp


class HealthcheckAccessLogger(AccessLogger):
    """Suppresses access logging for successful GET /health requests to avoid polluting logs."""

    def log(self, request: web.Request, response: web.StreamResponse, time: float) -> None:
        if request.path == "/health" and response.status == 200:
            return
        super().log(request, response, time)


@web.middleware
async def _http_correlation_middleware(request: web.Request, handler):
    """Give every public HTTP request the same request_id as Telegram updates."""
    try:
        set_request_id(uuid.uuid4().hex[:8])
        return await handler(request)
    finally:
        # aiohttp serves each request in its own task; resetting is a
        # belt-and-braces guard for reused contexts.
        set_request_id("system")


_http_limiter = HttpRateLimiter()


@web.middleware
async def _http_rate_limit_middleware(request: web.Request, handler):
    """Process-local rate limiting for incoming HTTP webhooks and endpoints.

    Exempt /health and YooKassa webhooks from strict rate limiting:
    - /health receives frequent container health probes
    - YooKassa webhooks receive legitimate burst batches and retries and are
      already strictly guarded by official IP allowlisting
    """
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


async def start_webhook_server(port: int):
    # YooKassa payloads are small. Reject unexpectedly large request bodies
    # before JSON parsing to limit memory use on the public endpoint.
    app = web.Application(client_max_size=64 * 1024)
    app["trusted_proxies"] = get_settings().TRUSTED_PROXIES
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


async def _stop_broadcast_tasks():
    for event in _broadcast_stop_events.values():
        event.set()

    tasks = list(_background_tasks)
    for task in tasks:
        task.cancel()

    if tasks:
        await asyncio.wait(tasks, timeout=10)

    logger.info("Broadcast tasks stopped (%s tasks)", len(tasks))


async def main():
    settings = None
    bot = None
    dp = None
    webhook_runner = None

    try:
        settings = get_settings()
        ci_test_mode = _is_ci_test_mode()
        shutdown_event.clear()

        # P3-2: Защита от потери backup.agekey
        config_dir = os.getenv("JUST1KBOT_CONFIG_DIR", "/etc/just1kbot")
        from pathlib import Path
        backup_key_path = Path(config_dir) / "backup.agekey"

        is_docker = os.getenv("DOCKER_DEPLOYMENT", "false").lower() == "true"

        if settings.DB_ENCRYPTION_KEY and not is_docker and not await aiofiles.os.path.exists(
            str(backup_key_path)
        ):
            logger.critical(
                "CRITICAL WARNING: DB_ENCRYPTION_KEY is present but "
                f"{backup_key_path} is missing!"
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

        logger.info(
            "🔄 Bot started — all in-memory operation locks "
            "cleared (restart)."
        )

        bot, dp = await setup_bot()

        webhook_runner = await start_webhook_server(
            settings.YOOKASSA_WEBHOOK_PORT
        )

        if ci_test_mode:
            logger.info(
                "CI_TEST_MODE enabled; skipping Telegram polling, "
                "pending broadcast resume and background workers"
            )
        else:
            await resume_pending_broadcasts(bot)
            logger.info("Pending broadcasts resumed (if any)")

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

        try:
            await _stop_broadcast_tasks()
        except Exception as e:
            logger.error("Error stopping broadcast tasks: %s", e)

        try:
            await stop_clean_chat_worker()
        except Exception as e:
            logger.error("Error stopping CleanChat worker: %s", e)

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
            await close_http_session()
        except Exception as e:
            logger.error("Failed to close Amnezia HTTP session: %s", e)

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
