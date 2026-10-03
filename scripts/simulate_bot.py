# ruff: noqa: E402
"""
Just1kBot — Self-Contained Enterprise Simulation Testbed
=========================================================
Runs a full, interactive Telegram Bot simulation with in-memory / SQLite database,
fully mocked external APIs (Amnezia VPN, YooKassa payment gateway, Telegram effects),
and automatic user onboarding / seeding without requiring live production credentials.

Usage:
    python scripts/simulate_bot.py --token YOUR_BOT_TOKEN
    OR set environment variable BOT_TOKEN (or TEST_BOT_TOKEN)

Features:
    - Zero external cloud/server dependencies (runs anywhere with Python 3.11+).
    - 100% full bot UI and logic: device management, payments, tariff change,
      referral program, admin dashboard, and modern Bot API 10.x effects.
    - PostgreSQL emulation on SQLite (advisory locks, JSONB, partial unique indexes).
    - Dynamic auto-seeding for any connected Telegram user.
"""

from __future__ import annotations

import argparse
import asyncio
from decimal import Decimal
import json
import logging
import os
from pathlib import Path
import sys

# Add repository root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Setup safe simulation environment defaults before importing any application modules
from cryptography.fernet import Fernet

_dummy_fernet = os.getenv("DB_ENCRYPTION_KEY") or Fernet.generate_key().decode()
# TEST-ONLY dummy defaults for local simulation (never production credentials).
os.environ.setdefault("BOT_TOKEN", "123456789:TEST_ONLY_DUMMY_TOKEN_DO_NOT_USE_IN_PROD")
os.environ.setdefault("ADMIN_IDS", "[999999999]")
os.environ.setdefault("SUPPORT_USERNAME", "just1k_support")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("DB_ENCRYPTION_KEY", _dummy_fernet)
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("REDIS_PASSWORD", "sim_redis_pass_123")
os.environ.setdefault("YOOKASSA_SHOP_ID", "mock_shop")
os.environ.setdefault("YOOKASSA_SECRET_KEY", "TEST_ONLY_SIM_SECRET_KEY_123")
os.environ.setdefault("YOOKASSA_RETURN_URL", "https://t.me/{bot_username}?start=pay_success")
os.environ.setdefault("YOOKASSA_WEBHOOK_PORT", "8080")
os.environ.setdefault("DOMAIN", "sim.just1k.net")
os.environ.setdefault("SSL_EMAIL", "sim@just1k.net")
os.environ.setdefault("CHANNEL_URL", "https://t.me/just1k_channel")
os.environ.setdefault("RULES_URL", "https://just1k.net/rules")
os.environ.setdefault("FAQ_URL", "https://just1k.net/faq")

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BotCommand,
    BotCommandScopeDefault,
    MenuButtonCommands,
)
from aiogram.utils.chat_action import ChatActionMiddleware
from sqlalchemy import (
    DateTime,
    Integer,
    select,
    text,
)
from sqlalchemy.ext.asyncio import (
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import CheckConstraint

from bot import texts
from bot.middlewares.action_lock import ActionLockMiddleware
from bot.middlewares.ban_check import BanCheckMiddleware
from bot.middlewares.correlation import CorrelationMiddleware
from bot.middlewares.throttling import ThrottlingMiddleware
from bot.middlewares.user_context import UserContextMiddleware
from config.constants import AMNEZIA_PROTOCOL, XRAY_PROTOCOL
from config.settings import Settings
from config.tariffs import DEFAULT_TARIFFS_SEEDS
import database.connection as db_conn
from database.connection import session_scope
from database.models import (
    Base,
    Server,
    Tariff,
    TariffVersion,
)
from scripts.simulate import service_mocks  # noqa: F401 (applies mock patching on import)
from scripts.simulate.db_shims import UTCDateTime
from scripts.simulate.seeding import SimulationAutoSeedMiddleware

# --- 5. MAIN SIMULATION RUNNER ---

async def run_simulation(args: argparse.Namespace):
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s - %(levelname)s - %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logger = logging.getLogger("simulation")
    logger.info("--- Initializing Just1kBot Simulation Environment ---")

    # Set up dummy encryption key if not provided
    sim_fernet_key = os.getenv("DB_ENCRYPTION_KEY") or Fernet.generate_key().decode()

    # Parse admin IDs
    admin_ids = []
    if args.admin_id:
        raw_admin = args.admin_id.strip()
        if raw_admin.startswith("[") and raw_admin.endswith("]"):
            try:
                parsed_list = json.loads(raw_admin)
                if isinstance(parsed_list, list):
                    admin_ids = [int(x) for x in parsed_list if str(x).isdigit()]
            except Exception:
                pass
        if not admin_ids:
            for aid_raw in raw_admin.strip("[]").split(","):
                aid = aid_raw.strip().strip("'\"")
                if aid.isdigit():
                    admin_ids.append(int(aid))
    if not admin_ids:
        admin_ids = [999999999]  # Dummy non-existent admin ID to satisfy validator

    os.environ["BOT_TOKEN"] = args.token
    os.environ["ADMIN_IDS"] = json.dumps(admin_ids)
    os.environ["DATABASE_URL"] = args.db_url
    os.environ["DB_ENCRYPTION_KEY"] = sim_fernet_key

    # Override application settings
    mock_settings = Settings(
        BOT_TOKEN=args.token,
        DATABASE_URL=args.db_url,
        DB_ENCRYPTION_KEY=sim_fernet_key,
        REDIS_URL="redis://localhost:6379/0",
        REDIS_PASSWORD="sim_redis_pass_123",
        ADMIN_IDS=admin_ids,
        YOOKASSA_SHOP_ID="mock_shop",
        YOOKASSA_SECRET_KEY="TEST_ONLY_SIM_SECRET_KEY_123",
        YOOKASSA_RETURN_URL="https://t.me/{bot_username}?start=pay_success",
        YOOKASSA_WEBHOOK_PORT=8080,
        DOMAIN="sim.just1k.net",
        SSL_EMAIL="sim@just1k.net",
        SUPPORT_USERNAME="just1k_support",
        CHANNEL_URL="https://t.me/just1k_channel",
        RULES_URL="https://just1k.net/rules",
        FAQ_URL="https://just1k.net/faq",
    )
    import config.settings
    config.settings.get_settings.cache_clear()
    config.settings.get_settings = lambda: mock_settings

    for mod in list(sys.modules.values()):
        if (
            mod
            and getattr(mod, "__name__", "").startswith(
                ("bot", "config", "services", "database", "utils", "integrations")
            )
            and hasattr(mod, "get_settings")
        ):
            try:
                mod.get_settings = lambda: mock_settings
            except Exception:
                pass

    # Database initialization (SQLite or real PostgreSQL)
    is_sqlite = args.db_url.startswith("sqlite")
    if is_sqlite:
        engine = create_async_engine(
            args.db_url,
            echo=False,
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        db_conn._engine = engine
        db_conn._sessionmaker = session_factory

        # Prepare SQLite Schema
        for table in Base.metadata.tables.values():
            table.constraints = {
                c for c in table.constraints if not isinstance(c, CheckConstraint)
            }
            is_single_pk = len(table.primary_key.columns) == 1
            for col in table.columns:
                if isinstance(col.type, DateTime):
                    col.type = UTCDateTime()
                if col.primary_key:
                    col.type = Integer()
                    col.autoincrement = is_single_pk
                if col.server_default is not None:
                    sd = str(getattr(col.server_default, "arg", ""))
                    if "::" in sd or "now()" in sd.lower():
                        col.server_default = None
            table.indexes.clear()

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            # Partial unique indexes required by ON CONFLICT clauses
            await conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_tariff_quotes_active_change_user "
                    "ON tariff_quotes (user_id) WHERE operation_type='change' AND status='active'"
                )
            )
        logger.info("SQLite database schema initialized.")
    else:
        logger.info("Connecting to real PostgreSQL database: %s", args.db_url.split("@")[-1])
        from database.connection import init_db
        engine, session_factory = await init_db()
        logger.info("PostgreSQL connection pool initialized.")

    # Seed baseline tariffs and servers if not present
    async with session_factory() as session:
        for t_data in DEFAULT_TARIFFS_SEEDS:
            existing = await session.scalar(
                select(Tariff).where(
                    Tariff.duration_days == t_data["duration_days"],
                    Tariff.device_limit == t_data["device_limit"],
                )
            )
            if not existing:
                t = Tariff(
                    name=t_data["name"],
                    description=t_data.get("description"),
                    duration_days=t_data["duration_days"],
                    device_limit=t_data["device_limit"],
                    price_rub=t_data["price_rub"],
                    sort_order=t_data.get("sort_order", 0),
                    is_active=True,
                )
                session.add(t)
                await session.flush()
                tv = TariffVersion(
                    tariff_id=t.id,
                    version_number=1,
                    name_snapshot=t.name,
                    duration_hours=t.duration_days * 24,
                    device_limit=t.device_limit,
                    price_rub=Decimal(t.price_rub),
                    currency="RUB",
                )
                session.add(tv)
            else:
                existing_tv = await session.scalar(
                    select(TariffVersion).where(TariffVersion.tariff_id == existing.id)
                )
                if not existing_tv:
                    tv = TariffVersion(
                        tariff_id=existing.id,
                        version_number=1,
                        name_snapshot=existing.name,
                        duration_hours=existing.duration_days * 24,
                        device_limit=existing.device_limit,
                        price_rub=Decimal(existing.price_rub),
                        currency="RUB",
                    )
                    session.add(tv)
        await session.commit()

        servers = [
            Server(
                id=1,
                name="Нидерланды #1 (Амстердам)",
                country_flag="🇳🇱",
                api_url="http://nl1.just1k.net:8080",
                api_key="enc_key_nl",
                protocol=AMNEZIA_PROTOCOL,
                is_active=True,
                max_clients=100,
            ),
            Server(
                id=2,
                name="Германия #1 (Франкфурт)",
                country_flag="🇩🇪",
                api_url="http://de1.just1k.net:8080",
                api_key="enc_key_de",
                protocol=AMNEZIA_PROTOCOL,
                is_active=True,
                max_clients=100,
            ),
            Server(
                id=3,
                name="Швеция #1 (Стокгольм)",
                country_flag="🇸🇪",
                api_url="http://se1.just1k.net:8080",
                api_key="enc_key_se",
                protocol=AMNEZIA_PROTOCOL,
                is_active=True,
                max_clients=100,
            ),
            Server(
                id=4,
                name="Финляндия #1 (Хельсинки)",
                country_flag="🇫🇮",
                api_url="http://fi1.just1k.net:8080",
                api_key="enc_key_fi",
                protocol=AMNEZIA_PROTOCOL,
                is_active=True,
                max_clients=100,
            ),
            Server(
                id=5,
                name="Россия Xray Origin",
                country_flag="🇷🇺",
                api_url="http://ru-origin.just1k.net:8444",
                api_key="enc_key_xray",
                protocol=XRAY_PROTOCOL,
                capabilities=["xray_origin"],
                xray_instance_epoch="sim_epoch_1",
                extra_data={
                    "relays": [
                        {
                            "code": "de-relay-01",
                            "tag": "de-relay-01",
                            "name": "Германия Релей #1",
                            "flag": "🇩🇪",
                            "ip": "185.190.140.1",
                            "port": 10443,
                            "healthy": True,
                            "status": "online",
                            "rtt_ms": 14.2,
                        },
                        {
                            "code": "se-relay-01",
                            "tag": "se-relay-01",
                            "name": "Швеция Релей #1",
                            "flag": "🇸🇪",
                            "ip": "194.26.229.2",
                            "port": 10443,
                            "healthy": True,
                            "status": "online",
                            "rtt_ms": 28.5,
                        },
                    ],
                    "origin_tag": "RU-Origin",
                    "profile_title": "Just1k INCY White Internet",
                },
                is_active=True,
                max_clients=100,
            ),
        ]
        for s in servers:
            existing_s = await session.scalar(
                select(Server).where(
                    (Server.id == s.id) | (Server.api_url == s.api_url)
                )
            )
            if not existing_s:
                session.add(s)
            else:
                existing_s.name = s.name
                existing_s.country_flag = s.country_flag
                existing_s.api_url = s.api_url
                existing_s.protocol = s.protocol
                if s.capabilities:
                    existing_s.capabilities = s.capabilities
                if s.xray_instance_epoch:
                    existing_s.xray_instance_epoch = s.xray_instance_epoch
                if s.extra_data:
                    existing_s.extra_data = s.extra_data
        await session.commit()
    logger.info("Tariffs and high-speed simulation servers seeded successfully.")

    if args.maintenance:
        async with session_scope() as session:
            from services.maintenance_service import MaintenanceService
            await MaintenanceService.enable(session, admin_id=999999999, message="⚙️ Ведутся технические работы. Пожалуйста, попробуйте позже.")
            logger.info("⚙️ [MAINTENANCE] Maintenance mode enabled.")

    # Initialize Telegram Bot & Dispatcher
    bot = Bot(token=args.token)
    me = await bot.get_me()
    logger.info("Connected to Telegram Bot API: @%s (%s)", me.username, me.id)

    redis_url = getattr(args, "redis_url", None) or os.getenv("REDIS_URL")
    if redis_url and not is_sqlite:
        from aiogram.fsm.storage.redis import RedisStorage
        storage = RedisStorage.from_url(redis_url)
        logger.info("Using real Redis FSM storage: %s", redis_url.split("@")[-1])
    else:
        storage = MemoryStorage()
        logger.info("Using in-memory FSM storage")

    dp = Dispatcher(storage=storage)

    from bot.middlewares.db_session import DBSessionMiddleware

    # Middlewares
    dp.message.middleware(CorrelationMiddleware())
    dp.callback_query.middleware(CorrelationMiddleware())

    dp.message.middleware(DBSessionMiddleware())
    dp.callback_query.middleware(DBSessionMiddleware())

    # Auto-seed middleware for new users
    auto_seed = SimulationAutoSeedMiddleware(
        real_balance=Decimal(args.seed_balance_real),
        bonus_balance=Decimal(args.seed_balance_bonus),
        enabled=not args.no_auto_seed,
        allow_admin_seed=args.allow_admin_seed,
    )
    dp.message.middleware(auto_seed)
    dp.callback_query.middleware(auto_seed)

    dp.message.middleware(UserContextMiddleware())
    dp.callback_query.middleware(UserContextMiddleware())
    dp.message.middleware(BanCheckMiddleware())
    dp.callback_query.middleware(BanCheckMiddleware())
    dp.message.middleware(ThrottlingMiddleware())
    dp.callback_query.middleware(ThrottlingMiddleware())
    dp.callback_query.middleware(ActionLockMiddleware())
    dp.message.middleware(ChatActionMiddleware())

    # Routers
    from bot.handlers.admin import admin_router
    from bot.handlers.connection import router as connection_router
    from bot.handlers.fallback import router as fallback_router
    from bot.handlers.payment import router as payment_router
    from bot.handlers.referral import router as referral_router
    from bot.handlers.start import router as start_router
    from bot.handlers.support import router as support_router
    from bot.handlers.white_internet import router as white_internet_router

    for r in [
        start_router,
        referral_router,
        connection_router,
        white_internet_router,
        support_router,
        payment_router,
        admin_router,
        fallback_router,
    ]:
        r._parent_router = None
        dp.include_router(r)

    # Set commands
    commands = [BotCommand(command="start", description=texts.BOT_START_DESCRIPTION)]
    await bot.set_my_commands(commands, scope=BotCommandScopeDefault())
    await bot.set_chat_menu_button(menu_button=MenuButtonCommands())

    # Clear pending updates
    await bot.delete_webhook(drop_pending_updates=True)

    # Start background workers
    from services.workers import start_background_workers, stop_background_workers
    await start_background_workers(bot)
    logger.info("Real enterprise background workers started successfully.")

    logger.info("=" * 60)
    logger.info("BOT IS RUNNING IN LIVE PROD SIMULATION: @%s", me.username)
    logger.info("All tariffs, servers, device creation, background workers & payments are 100%% active.")
    logger.info("=" * 60)

    try:
        await dp.start_polling(bot, handle_signals=False)
    finally:
        try:
            await stop_background_workers()
        except Exception:
            pass
        if hasattr(dp.storage, "close"):
            try:
                await dp.storage.close()
            except Exception:
                pass
        await bot.session.close()
        from database.connection import close_db
        await close_db()
        logger.info("Simulation stopped cleanly.")


def main():
    parser = argparse.ArgumentParser(
        description="Just1kBot Production Simulation Testbed"
    )
    parser.add_argument(
        "--token",
        type=str,
        default=os.getenv("BOT_TOKEN") or os.getenv("TEST_BOT_TOKEN"),
        help="Telegram Bot Token (or set BOT_TOKEN / TEST_BOT_TOKEN env var)",
    )
    parser.add_argument(
        "--admin-id",
        type=str,
        default=os.getenv("ADMIN_IDS", ""),
        help="Comma-separated Telegram Admin User IDs",
    )
    parser.add_argument(
        "--db-url",
        type=str,
        default=os.getenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:"),
        help="Database URL (default: sqlite+aiosqlite:///:memory:)",
    )
    parser.add_argument(
        "--redis-url",
        type=str,
        default=os.getenv("REDIS_URL"),
        help="Redis URL (default: None for in-memory FSM storage)",
    )
    parser.add_argument(
        "--seed-balance-real",
        type=int,
        default=350,
        help="Initial real balance in RUB for auto-seeded user (default: 350)",
    )
    parser.add_argument(
        "--seed-balance-bonus",
        type=int,
        default=150,
        help="Initial bonus balance in RUB for auto-seeded user (default: 150)",
    )
    parser.add_argument(
        "--no-auto-seed",
        action="store_true",
        help="Disable automatic onboarding and seeding of new users",
    )
    parser.add_argument(
        "--allow-admin-seed",
        action="store_true",
        help="Grant admin rights to connecting testers (dev-only, off by default)",
    )
    parser.add_argument(
        "--maintenance",
        action="store_true",
        help="Enable maintenance mode on startup",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO)",
    )

    args = parser.parse_args()

    if not args.token:
        print(
            "ERROR: Bot token is required. Pass --token YOUR_TOKEN or set BOT_TOKEN / TEST_BOT_TOKEN environment variable.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        asyncio.run(run_simulation(args))
    except (KeyboardInterrupt, SystemExit):
        print("\nSimulation terminated by user.")


if __name__ == "__main__":
    main()
