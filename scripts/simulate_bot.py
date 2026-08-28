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
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import logging
import os
from pathlib import Path
import sys
import uuid

# Add repository root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Setup safe simulation environment defaults before importing any application modules
from cryptography.fernet import Fernet

_dummy_fernet = os.getenv("DB_ENCRYPTION_KEY") or Fernet.generate_key().decode()
os.environ.setdefault("BOT_TOKEN", "123456789:AABBCcDdEeFfGgHhIiJjKkLlMmNnOoPpQqR")
os.environ.setdefault("ADMIN_IDS", "[999999999]")
os.environ.setdefault("SUPPORT_USERNAME", "just1k_support")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("DB_ENCRYPTION_KEY", _dummy_fernet)
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("REDIS_PASSWORD", "sim_redis_pass_123")
os.environ.setdefault("YOOKASSA_SHOP_ID", "mock_shop")
os.environ.setdefault("YOOKASSA_SECRET_KEY", "live_sim_secret_key_123")
os.environ.setdefault("YOOKASSA_RETURN_URL", "https://t.me/{bot_username}?start=pay_success")
os.environ.setdefault("YOOKASSA_WEBHOOK_PORT", "8080")
os.environ.setdefault("DOMAIN", "sim.just1k.net")
os.environ.setdefault("SSL_EMAIL", "sim@just1k.net")
os.environ.setdefault("CHANNEL_URL", "https://t.me/just1k_channel")
os.environ.setdefault("RULES_URL", "https://just1k.net/rules")
os.environ.setdefault("FAQ_URL", "https://just1k.net/faq")

import aiosqlite
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BotCommand,
    BotCommandScopeDefault,
    MenuButtonCommands,
    Update,
)
from aiogram.utils.chat_action import ChatActionMiddleware
from cryptography.fernet import Fernet
from sqlalchemy import (
    DateTime,
    Integer,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, BIGINT, JSONB
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import CheckConstraint
from sqlalchemy.types import TypeDecorator

from bot import texts
from bot.middlewares.action_lock import ActionLockMiddleware
from bot.middlewares.ban_check import BanCheckMiddleware
from bot.middlewares.correlation import CorrelationMiddleware
from bot.middlewares.throttling import ThrottlingMiddleware
from bot.middlewares.user_context import UserContextMiddleware
from config.constants import AMNEZIA_PROTOCOL
from config.settings import Settings
import database.connection as db_conn
from database.connection import DEFAULT_TARIFFS, session_scope
from database.models import (
    AccountLedgerEntry,
    Base,
    EntitlementEntry,
    PaidValueLedgerEntry,
    Payment,
    Server,
    Tariff,
    TariffQuote,
    TariffVersion,
    User,
    VPNProfile,
)
from services.account_topup import AccountTopupError, settle_succeeded_topup
import services.account_topup_refresh as topup_refresh
from services.amnezia_client import (
    AmneziaAPIResult,
    AmneziaClient,
    AmneziaClientCreateResponse,
    AmneziaClientListItem,
)
from services.yookassa_service import YooKassaResult, YooKassaService
from utils.datetime_helpers import now_utc
from utils.vpn_parser import encode_json_to_vpn_uri

# --- 1. SQLITE COMPILER & POSTGRESQL EMULATION SHIMS ---

@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(type_, compiler, **kw):
    return "TEXT"

@compiles(ARRAY, "sqlite")
def _compile_array_sqlite(type_, compiler, **kw):
    return "TEXT"

@compiles(BIGINT, "sqlite")
def _compile_bigint_sqlite(type_, compiler, **kw):
    return "INTEGER"

# Intercept aiosqlite connection creation to register PostgreSQL emulator functions
_orig_aiosqlite_connect = aiosqlite.connect

def _custom_aiosqlite_connect(*args, **kwargs):
    kwargs["check_same_thread"] = False
    conn = _orig_aiosqlite_connect(*args, **kwargs)
    orig_connect_coro = conn._connect

    async def patched_connect():
        c = await orig_connect_coro()
        await c.create_function("pg_advisory_xact_lock", 1, lambda x: 1)
        await c.create_function("pg_advisory_xact_lock", 2, lambda x, y: 1)
        await c.create_function("pg_advisory_lock", 1, lambda x: 1)
        await c.create_function("pg_advisory_lock", 2, lambda x, y: 1)
        await c.create_function("pg_advisory_unlock", 1, lambda x: 1)
        await c.create_function("pg_advisory_unlock", 2, lambda x, y: 1)
        await c.create_function("trunc", 1, lambda x: int(x) if x is not None else 0)
        await c.create_function("is_nonnegative_integer_json_array", 1, lambda x: 1)
        return c

    conn._connect = patched_connect
    return conn

aiosqlite.connect = _custom_aiosqlite_connect

# Force SQLite datetimes to be loaded as timezone-aware UTC objects
class UTCDateTime(TypeDecorator):
    impl = DateTime
    cache_ok = True

    def process_result_value(self, value, dialect):
        if value is not None:
            if isinstance(value, str):
                for fmt in (
                    "%Y-%m-%d %H:%M:%S.%f",
                    "%Y-%m-%d %H:%M:%S",
                    "%Y-%m-%dT%H:%M:%S.%f",
                    "%Y-%m-%dT%H:%M:%S",
                    "%Y-%m-%d %H:%M:%S.%f%z",
                    "%Y-%m-%d %H:%M:%S%z",
                ):
                    try:
                        value = datetime.strptime(value, fmt)
                        break
                    except ValueError:
                        pass
            if isinstance(value, datetime) and value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
        return value


# --- 3. AMNEZIA VPN & YOOKASSA MOCK GENERATORS ---

def generate_mock_amnezia_vpn_uri(
    client_name: str,
    peer_id: str,
    host: str = "nl1.just1k.net",
) -> str:
    """Generate a realistic AmneziaWG 2.0 configuration URI with obfuscation parameters."""
    client_priv = f"MOCK_PRIVKEY_{peer_id[:8]}=="
    server_pub = "MOCK_PUBKEY_SERVER_NL=="
    conf_str = (
        f"[Interface]\n"
        f"PrivateKey = {client_priv}\n"
        f"Address = 10.8.0.2/32\n"
        f"DNS = 1.1.1.1, 8.8.8.8\n"
        f"Jc = 4\n"
        f"Jmin = 40\n"
        f"Jmax = 70\n"
        f"S1 = 15\n"
        f"S2 = 30\n"
        f"S3 = 10\n"
        f"S4 = 20\n"
        f"H1 = 1\n"
        f"H2 = 2\n"
        f"H3 = 3\n"
        f"H4 = 4\n\n"
        f"[Peer]\n"
        f"PublicKey = {server_pub}\n"
        f"Endpoint = {host}:51820\n"
        f"AllowedIPs = 0.0.0.0/0, ::/0\n"
        f"PersistentKeepalive = 25\n"
    )
    last_cfg = {
        "hostName": host,
        "port": 51820,
        "client_ip": "10.8.0.2/32",
        "client_priv_key": client_priv,
        "server_pub_key": server_pub,
        "Jc": 4, "Jmin": 40, "Jmax": 70,
        "S1": 15, "S2": 30, "S3": 10, "S4": 20,
        "H1": 1, "H2": 2, "H3": 3, "H4": 4,
        "config": conf_str,
        "mtu": "1280",
        "persistent_keep_alive": 25,
        "allowed_ips": ["0.0.0.0/0", "::/0"],
    }
    data = {
        "containers": [
            {
                "awg": {
                    "last_config": json.dumps(last_cfg, ensure_ascii=False),
                    "protocol_version": "2",
                }
            }
        ],
        "defaultContainer": "awg",
        "description": f"just1k VPN - {client_name}",
        "dns1": "1.1.1.1",
        "dns2": "8.8.8.8",
        "hostName": host,
        "port": 51820,
    }
    return encode_json_to_vpn_uri(data)


async def mock_amnezia_create_user_result(self, client_name: str, expires_at=None) -> AmneziaAPIResult:
    logger = logging.getLogger("simulation.amnezia")
    mock_peer_id = f"peer_{uuid.uuid4().hex[:8]}"
    mock_vpn_uri = generate_mock_amnezia_vpn_uri(client_name, mock_peer_id)
    logger.info("🔌 [MOCK AMNEZIA] Generated simulated VPN profile '%s' (%s)", client_name, mock_peer_id)
    resp = AmneziaClientCreateResponse(
        id=mock_peer_id,
        client_name=client_name,
        config=mock_vpn_uri,
        raw_config=mock_vpn_uri,
    )
    return AmneziaAPIResult(ok=True, value=resp, error_kind=None, status_code=200, retryable=False, ambiguous=False)


async def mock_amnezia_delete_user_result(self, client_id: str) -> AmneziaAPIResult:
    logger = logging.getLogger("simulation.amnezia")
    logger.info("🗑 [MOCK AMNEZIA] Deleted simulated VPN profile (%s)", client_id)
    return AmneziaAPIResult(ok=True, value=None, error_kind=None, status_code=200, retryable=False, ambiguous=False)


async def mock_amnezia_get_all_clients(self):
    return [
        AmneziaClientListItem(id="peer_sim_nl_iphone", username="iPhone 16 Pro", peer_name="iPhone 16 Pro"),
        AmneziaClientListItem(id="peer_sim_de_macbook", username="MacBook Pro M3", peer_name="MacBook Pro M3"),
    ]


async def mock_yookassa_create_payment_result(cls, payload: dict, *, idempotency_key: str | None = None, **kwargs) -> YooKassaResult:
    logger = logging.getLogger("simulation.yookassa")
    amount_str = payload.get("amount", {}).get("value", "100.00")
    order_id = payload.get("metadata", {}).get("order_id", str(uuid.uuid4())[:8])
    mock_id = f"mock_pay_{order_id}"
    logger.info("💳 [MOCK YOOKASSA] Created test invoice for %s RUB (ID: %s)", amount_str, mock_id)
    return YooKassaResult(
        ok=True,
        value={
            "id": mock_id,
            "status": "pending",
            "paid": False,
            "amount": {"value": amount_str, "currency": "RUB"},
            "confirmation": {
                "type": "redirect",
                "confirmation_url": f"https://t.me/just1kbot?start=pay_test_{mock_id}",
            },
            "created_at": now_utc().isoformat(),
        },
        status_code=200,
    )


async def mock_yookassa_get_payment_result(cls, payment_id: str, **kwargs) -> YooKassaResult:
    logger = logging.getLogger("simulation.yookassa")
    logger.info("✅ [MOCK YOOKASSA] Verifying payment %s -> AUTO-APPROVING AS SUCCEEDED", payment_id)
    return YooKassaResult(
        ok=True,
        value={
            "id": payment_id,
            "status": "succeeded",
            "paid": True,
            "amount": {"value": "100.00", "currency": "RUB"},
            "created_at": now_utc().isoformat(),
            "captured_at": now_utc().isoformat(),
        },
        status_code=200,
    )


async def mock_request_topup_status_refresh(
    session: AsyncSession,
    *,
    payment_id: int,
    source: str = "user_refresh",
    bot: Bot | None = None,
) -> Payment:
    payment = await session.scalar(
        select(Payment).where(Payment.id == payment_id).with_for_update()
    )
    if payment is None:
        raise AccountTopupError("topup_not_found")

    payment.provider_status = "succeeded"
    payment.provider_confirmed_at = now_utc()
    payment.paid_at = now_utc()
    payment.checkout_status = "completed"
    if payment.fulfillment_status not in {"succeeded", "reversed", "manual_review"}:
        await settle_succeeded_topup(session, payment=payment, source="simulation_refresh", bot=bot)
    logging.getLogger("simulation.topup").info(
        "💰 [TOPUP REFRESH] Succeeded and credited %s RUB to user %s",
        payment.amount,
        payment.user_id,
    )
    return payment


async def mock_amnezia_healthcheck(self) -> bool:
    return True


async def mock_amnezia_get_server_load(self, timeout: float = 10.0) -> dict | None:
    return {
        "cpu_percent": 12.5,
        "ram_percent": 34.0,
        "disk_percent": 25.0,
        "active_peers": 3,
    }


# Apply monkeypatches to external service clients
AmneziaClient.create_user_result = mock_amnezia_create_user_result
AmneziaClient.delete_user_result = mock_amnezia_delete_user_result
AmneziaClient.get_all_clients = mock_amnezia_get_all_clients
AmneziaClient.healthcheck = mock_amnezia_healthcheck
AmneziaClient.get_server_load = mock_amnezia_get_server_load
YooKassaService.create_payment_result = classmethod(mock_yookassa_create_payment_result)
YooKassaService.get_payment_result = classmethod(mock_yookassa_get_payment_result)
topup_refresh.request_topup_status_refresh = mock_request_topup_status_refresh


# --- 4. DYNAMIC USER AUTO-SEEDING MIDDLEWARE ---

class SimulationAutoSeedMiddleware:
    """Automatically seeds newly connected Telegram users with realistic account state."""

    def __init__(
        self,
        real_balance: Decimal = Decimal(350),
        bonus_balance: Decimal = Decimal(150),
        enabled: bool = True,
    ):
        self.real_balance = real_balance
        self.bonus_balance = bonus_balance
        self.enabled = enabled

    async def __call__(self, handler, event: Update, data: dict):
        if not self.enabled:
            return await handler(event, data)

        user = getattr(event, "from_user", None)
        if not user:
            return await handler(event, data)

        async with session_scope() as session:
            db_user = await session.scalar(
                select(User).where(User.telegram_id == user.id)
            )
            if not db_user:
                    tariff = await session.scalar(
                        select(Tariff).where(Tariff.is_active.is_(True)).order_by(Tariff.id.asc()).limit(1)
                    )
                    tariff_id = tariff.id if tariff else None
                    tv = await session.scalar(
                        select(TariffVersion).where(TariffVersion.tariff_id == tariff_id).limit(1)
                    ) if tariff_id else None
                    tv_id = tv.id if tv else None

                    server = await session.scalar(
                        select(Server).where(Server.is_active.is_(True)).order_by(Server.id.asc()).limit(1)
                    )
                    server_id = server.id if server else 1

                    # Create user record
                    db_user = User(
                        telegram_id=user.id,
                        username=user.username or f"user_{user.id}",
                        first_name=user.first_name or "Tester",
                        device_limit=5,
                        current_tariff_id=tariff_id,
                        subscription_end=now_utc() + timedelta(days=28),
                        created_at=now_utc() - timedelta(days=2),
                    )
                    session.add(db_user)
                    await session.flush()

                    # Seed initial payment & ledger entries
                    seed_pay = Payment(
                        user_id=db_user.id,
                        amount=self.real_balance,
                        currency="RUB",
                        public_order_id=f"order_{uuid.uuid4().hex[:8]}",
                        provider_idempotency_key=f"idem_{uuid.uuid4().hex[:12]}",
                        provider_status="succeeded",
                        fulfillment_status="succeeded",
                        reconciliation_status="ok",
                        checkout_status="active",
                        ui_visible=True,
                        created_at=now_utc(),
                        paid_at=now_utc(),
                        credited_at=now_utc(),
                    )
                    session.add(seed_pay)
                    await session.flush()

                    ts = int(now_utc().timestamp() * 1000)
                    entry_real = AccountLedgerEntry(
                        id=ts + 1,
                        user_id=db_user.id,
                        amount=self.real_balance,
                        currency="RUB",
                        entry_type="payment_credit",
                        payment_id=seed_pay.id,
                        idempotency_key=f"seed_real_{uuid.uuid4().hex}",
                        metadata_={"note": "Initial simulation balance"},
                        created_at=now_utc(),
                    )
                    entry_bonus = AccountLedgerEntry(
                        id=ts + 2,
                        user_id=db_user.id,
                        amount=self.bonus_balance,
                        currency="RUB",
                        entry_type="admin_adjustment",
                        idempotency_key=f"seed_bonus_{uuid.uuid4().hex}",
                        metadata_={
                            "source_type": "referral_referrer_bonus",
                            "reason": "welcome_bonus",
                        },
                        created_at=now_utc(),
                    )

                    # Initial quote, entitlement and paid value ledger
                    init_quote = TariffQuote(
                        public_id=uuid.uuid4(),
                        user_id=db_user.id,
                        target_tariff_version_id=tv_id,
                        operation_type="purchase",
                        current_paid_hours=0,
                        current_paid_value_rub=Decimal(0),
                        bonus_hours=0,
                        amount_due_rub=Decimal(180),
                        resulting_paid_hours=720,
                        resulting_paid_value_rub=Decimal(180),
                        resulting_bonus_hours=0,
                        rounding_loss_hours=Decimal(0),
                        rounding_loss_value_rub=Decimal(0),
                        status="consumed",
                        consumed_at=now_utc() - timedelta(days=2),
                        expires_at=now_utc(),
                        created_at=now_utc() - timedelta(days=2),
                    )
                    session.add(init_quote)
                    await session.flush()

                    init_ent = EntitlementEntry(
                        beneficiary_user_id=db_user.id,
                        source_type="quote",
                        source_id=str(init_quote.id),
                        entry_type="account_purchase_grant",
                        days_delta=30,
                        hours_delta=720,
                        device_limit_snapshot=5,
                        tariff_id_snapshot=tariff_id,
                        created_at=now_utc() - timedelta(days=2),
                    )
                    init_pvl = PaidValueLedgerEntry(
                        user_id=db_user.id,
                        source_type="quote",
                        source_id=str(init_quote.id),
                        entry_type="account_purchase",
                        quote_id=init_quote.id,
                        paid_hours_delta=720,
                        paid_value_rub_delta=Decimal(180),
                        currency="RUB",
                        tariff_version_id=tv_id,
                        created_at=now_utc() - timedelta(days=2),
                    )
                    session.add_all([entry_real, entry_bonus, init_ent, init_pvl])

                    # Create 1 Active Device (iPhone)
                    prof = VPNProfile(
                        user_id=db_user.id,
                        server_id=server_id,
                        device_name="iPhone 16 Pro",
                        client_name="iPhone 16 Pro",
                        peer_id="peer_sim_nl_iphone",
                        raw_config=generate_mock_amnezia_vpn_uri(
                            "iPhone 16 Pro", "peer_sim_nl_iphone"
                        ),
                        provisioning_status="active",
                        desired_version=1,
                        is_active=True,
                        created_at=now_utc(),
                    )
                    session.add(prof)

                    # Seed 3 Mock Referrals for this user
                    ref1 = User(
                        telegram_id=user.id + 101,
                        username=f"friend_dmitry_{user.id}",
                        first_name="Дмитрий",
                        referred_by=user.id,
                        created_at=now_utc() - timedelta(days=10),
                    )
                    ref2 = User(
                        telegram_id=user.id + 102,
                        username=f"friend_elena_{user.id}",
                        first_name="Елена",
                        referred_by=user.id,
                        created_at=now_utc() - timedelta(days=5),
                    )
                    ref3 = User(
                        telegram_id=user.id + 103,
                        username=f"friend_sergey_{user.id}",
                        first_name="Сергей",
                        referred_by=user.id,
                        created_at=now_utc() - timedelta(days=2),
                    )
                    session.add_all([ref1, ref2, ref3])

                    logging.getLogger("simulation.seed").info(
                        "✨ [AUTO-SEED] Initialized user @%s (ID %s) with %s₽ real + %s₽ bonus + 1 active device + 3 referrals.",
                        db_user.username,
                        user.id,
                        self.real_balance,
                        self.bonus_balance,
                    )

        return await handler(event, data)


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
        for aid_raw in args.admin_id.split(","):
            aid = aid_raw.strip()
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
        YOOKASSA_SECRET_KEY="live_sim_secret_key_123",
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
        if mod and hasattr(mod, "get_settings"):
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
            # Ensure partial unique indexes required by ON CONFLICT clauses
            await conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_paid_value_conversion_quote "
                    "ON paid_value_ledger (quote_id) WHERE entry_type='tariff_conversion'"
                )
            )
            await conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_paid_value_account_purchase "
                    "ON paid_value_ledger (quote_id) WHERE entry_type='account_purchase'"
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
        for t_data in DEFAULT_TARIFFS:
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

        existing_server = await session.scalar(select(Server.id).limit(1))
        if not existing_server:
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
            ]
            session.add_all(servers)
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
    from bot.handlers.profile import router as profile_router
    from bot.handlers.start import router as start_router
    from bot.handlers.support import router as support_router
    from integrations import get_all_bot_routers

    integration_routers = get_all_bot_routers()

    for r in [
        start_router,
        profile_router,
        connection_router,
        *integration_routers,
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
