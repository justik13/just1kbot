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
from typing import Any
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

from contextlib import asynccontextmanager

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
from config.constants import AMNEZIA_PROTOCOL, XRAY_PROTOCOL
from config.enums import ServerHealthState, ServerLifecycleStatus
from config.settings import Settings
from config.tariffs import DEFAULT_TARIFFS_SEEDS
import database.connection as db_conn
from database.connection import session_scope
import database.dispute_models
import database.refund_models  # noqa: F401
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
from database.repositories.tariff_quotes_repo import get_or_create_current_version
from services.account_topup import AccountTopupError, settle_succeeded_topup
import services.account_topup as account_topup
import services.account_topup_refresh as topup_refresh
from services.amnezia_client import (
    AmneziaAPIResult,
    AmneziaClient,
    AmneziaClientCreateResponse,
    AmneziaClientListItem,
    AmneziaClientTraffic,
    AmneziaServerInfo,
)
from services.white_internet_service import WhiteInternetService
from services.xray_node_client import SyncResponse, SyncResult, XrayNodeClient
from services.yookassa_service import YooKassaResult, YooKassaService
from utils.datetime_helpers import now_utc
from utils.vpn_parser import encode_json_to_vpn_uri
import aiohttp

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
    kwargs["isolation_level"] = None
    conn = _orig_aiosqlite_connect(*args, **kwargs)
    orig_connect_coro = conn._connect

    async def patched_connect():
        c = await orig_connect_coro()
        await c.create_function("pg_try_advisory_lock", 1, lambda x: 1)
        await c.create_function("pg_try_advisory_lock", 2, lambda x, y: 1)
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

# Intercept synthetic ingress ping probes to prevent external network noise
_orig_aiohttp_get = aiohttp.ClientSession.get

def _mock_aiohttp_get(self, url, *args, **kwargs):
    if isinstance(url, str) and url.endswith("/ping"):
        class _MockResp:
            status = 200
            async def __aenter__(self):
                return self
            async def __aexit__(self, *a):
                pass
            async def text(self):
                return "pong"
        return _MockResp()
    return _orig_aiohttp_get(self, url, *args, **kwargs)

aiohttp.ClientSession.get = _mock_aiohttp_get

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


_SIMULATED_BOT_USERNAME: str = "just1kbot"

# In-memory simulated server peer/client state stores
_SIMULATED_AWG_PEERS: dict[str, dict[str, AmneziaClientListItem]] = {
    "http://nl1.just1k.net:8080": {
        "peer_sim_nl_iphone": AmneziaClientListItem(
            id="peer_sim_nl_iphone",
            username="iPhone 16 Pro",
            peer_name="iPhone 16 Pro",
            status="active",
            traffics=AmneziaClientTraffic(totalUpload=10485760, totalDownload=52428800),
        ),
    },
    "http://de1.just1k.net:8080": {
        "peer_sim_de_macbook": AmneziaClientListItem(
            id="peer_sim_de_macbook",
            username="MacBook Pro M3",
            peer_name="MacBook Pro M3",
            status="active",
            traffics=AmneziaClientTraffic(totalUpload=5242880, totalDownload=31457280),
        ),
    },
    "http://se1.just1k.net:8080": {},
    "http://fi1.just1k.net:8080": {},
}

_SIMULATED_XRAY_CLIENTS: dict[str, dict[str, Any]] = {}
_SIMULATED_PAYMENTS: dict[str, dict[str, Any]] = {}


async def mock_amnezia_create_user_result(self, client_name: str, expires_at=None) -> AmneziaAPIResult:
    logger = logging.getLogger("simulation.amnezia")
    mock_peer_id = f"peer_{uuid.uuid4().hex[:8]}"
    mock_vpn_uri = generate_mock_amnezia_vpn_uri(client_name, mock_peer_id)
    api_target = (self.api_url or "").rstrip("/")
    if api_target not in _SIMULATED_AWG_PEERS:
        _SIMULATED_AWG_PEERS[api_target] = {}
    peer_item = AmneziaClientListItem(
        id=mock_peer_id,
        username=client_name,
        peer_name=client_name,
        status="active",
        traffics=AmneziaClientTraffic(totalUpload=1048576, totalDownload=5242880),
    )
    _SIMULATED_AWG_PEERS[api_target][mock_peer_id] = peer_item
    logger.info("🔌 [MOCK AMNEZIA] Created simulated VPN profile '%s' (%s) on %s", client_name, mock_peer_id, api_target)
    resp = AmneziaClientCreateResponse(
        id=mock_peer_id,
        config=mock_vpn_uri,
        protocol=AMNEZIA_PROTOCOL,
    )
    return AmneziaAPIResult(ok=True, value=resp, error_kind=None, status_code=200, retryable=False, ambiguous=False)


async def mock_amnezia_delete_user_result(self, client_id: str) -> AmneziaAPIResult:
    logger = logging.getLogger("simulation.amnezia")
    api_target = (self.api_url or "").rstrip("/")
    if api_target in _SIMULATED_AWG_PEERS:
        _SIMULATED_AWG_PEERS[api_target].pop(client_id, None)
    logger.info("🗑 [MOCK AMNEZIA] Deleted simulated VPN profile (%s) on %s", client_id, api_target)
    return AmneziaAPIResult(ok=True, value=None, error_kind=None, status_code=200, retryable=False, ambiguous=False)


async def mock_amnezia_update_client_result(
    self,
    client_id: str,
    status: str | None = None,
    expires_at: int | None = None,
    clear_expires_at: bool = False,
) -> AmneziaAPIResult:
    api_target = (self.api_url or "").rstrip("/")
    if api_target in _SIMULATED_AWG_PEERS and client_id in _SIMULATED_AWG_PEERS[api_target]:
        if status is not None:
            _SIMULATED_AWG_PEERS[api_target][client_id].status = status
    return AmneziaAPIResult(ok=True, value=None, error_kind=None, status_code=200, retryable=False, ambiguous=False)


async def mock_amnezia_get_all_clients(self):
    api_target = (self.api_url or "").rstrip("/")
    return list(_SIMULATED_AWG_PEERS.get(api_target, {}).values())


async def mock_amnezia_healthcheck(self) -> bool:
    return True


async def mock_amnezia_get_server_load(self, timeout: float = 10.0) -> dict | None:
    api_target = (self.api_url or "").rstrip("/")
    active_peers = len(_SIMULATED_AWG_PEERS.get(api_target, {}))
    return {
        "cpu_percent": 14.5,
        "ram_percent": 32.0,
        "disk_percent": 27.0,
        "active_peers": active_peers,
    }


async def mock_amnezia_get_server_info(self):
    return AmneziaServerInfo(
        name="Simulated AWG Node",
        protocols=[AMNEZIA_PROTOCOL],
        maxPeers=100,
        serverMaxPeers=100,
    )


# --- Simulated Xray / White Internet Client ---

async def mock_xray_check_health(
    self, api_url: str, api_key: str
) -> tuple[bool, str | None, dict[str, Any] | None]:
    return True, "sim_epoch_1", {
        "status": "ok",
        "xray_running": True,
        "grpc_ok": True,
        "node_epoch": "sim_epoch_1",
        "boot_id": "sim_boot_1",
        "starttime": 1700000000,
        "cdn_domain": "cdn.yandex.net",
    }


async def mock_xray_sync_client(
    self,
    api_url: str,
    api_key: str,
    client_uuid: str,
    is_active: bool,
    version: int | None = None,
    expected_node_epoch: str | None = None,
    idempotency_key: str | None = None,
) -> SyncResponse:
    _SIMULATED_XRAY_CLIENTS[client_uuid] = {
        "is_active": is_active,
        "version": version or 1,
        "expected_node_epoch": expected_node_epoch or "sim_epoch_1",
    }
    logging.getLogger("simulation.xray").info(
        "🌐 [MOCK XRAY] Synced client %s (is_active=%s, version=%s)",
        client_uuid, is_active, version,
    )
    return SyncResponse(
        SyncResult.APPLIED,
        error=None,
        verified_epoch=expected_node_epoch or "sim_epoch_1",
        verified_inbounds=["just1k-wl-default", "just1k-wl-inbound-msk"],
        raw_data={"result": "applied", "inbounds": ["just1k-wl-default", "just1k-wl-inbound-msk"]},
    )


async def mock_xray_get_inventory(
    self,
    api_url: str,
    api_key: str,
    client_ids: list[str] | None = None,
) -> tuple[bool, dict[str, Any] | None, str | None]:
    clients = [
        {"id": uid, "state": "active" if info["is_active"] else "disabled"}
        for uid, info in _SIMULATED_XRAY_CLIENTS.items()
    ]
    if client_ids:
        clients = [c for c in clients if c["id"] in client_ids]
    return True, {"clients": clients}, None


async def mock_xray_remove_client(
    self,
    api_url: str,
    api_key: str,
    client_uuid: str,
    version: int | None = None,
) -> tuple[SyncResult, str | None]:
    _SIMULATED_XRAY_CLIENTS.pop(client_uuid, None)
    logging.getLogger("simulation.xray").info("🌐 [MOCK XRAY] Removed client %s", client_uuid)
    return SyncResult.APPLIED, None


async def mock_xray_get_traffic_snapshot(
    self, api_url: str, api_key: str
) -> tuple[str | None, str | None, int | None, dict[str, dict[str, int]] | None]:
    users = {}
    for uid, info in _SIMULATED_XRAY_CLIENTS.items():
        if info["is_active"]:
            users[uid] = {"uplink": 1048576, "downlink": 10485760}
    return "sim_epoch_1", "sim_boot_1", 1700000000, users


# --- Simulated YooKassa Payment Gateway ---

async def mock_yookassa_create_payment_result(cls, payload: dict, *, idempotency_key: str | None = None, **kwargs) -> YooKassaResult:
    logger = logging.getLogger("simulation.yookassa")
    amount_str = payload.get("amount", {}).get("value", "100.00")
    order_id = payload.get("metadata", {}).get("order_id", str(uuid.uuid4())[:8])
    local_payment_id = payload.get("metadata", {}).get("local_payment_id", "")
    mock_id = f"mock_pay_{order_id}"
    _SIMULATED_PAYMENTS[mock_id] = {
        "amount": amount_str,
        "order_id": order_id,
        "local_payment_id": local_payment_id,
    }
    logger.info("💳 [MOCK YOOKASSA] Created test invoice for %s RUB (ID: %s)", amount_str, mock_id)
    return YooKassaResult(
        ok=True,
        value={
            "id": mock_id,
            "status": "pending",
            "paid": False,
            "amount": {"value": amount_str, "currency": "RUB"},
            "metadata": {
                "order_id": order_id,
                "local_payment_id": local_payment_id,
            },
            "confirmation": {
                "type": "redirect",
                "confirmation_url": f"https://t.me/{_SIMULATED_BOT_USERNAME}?start=pay_{mock_id}",
            },
            "created_at": now_utc().isoformat(),
        },
        status_code=200,
    )


async def mock_yookassa_get_payment_result(cls, payment_id: str, **kwargs) -> YooKassaResult:
    logger = logging.getLogger("simulation.yookassa")
    sim_data = _SIMULATED_PAYMENTS.get(payment_id, {})
    amount_val = sim_data.get("amount", "100.00")
    order_id = sim_data.get("order_id", "")
    local_payment_id = sim_data.get("local_payment_id", "")
    logger.info("✅ [MOCK YOOKASSA] Checking payment %s -> returning status succeeded (%s RUB)", payment_id, amount_val)
    return YooKassaResult(
        ok=True,
        value={
            "id": payment_id,
            "status": "succeeded",
            "paid": True,
            "amount": {"value": amount_val, "currency": "RUB"},
            "metadata": {
                "order_id": order_id,
                "local_payment_id": local_payment_id,
            },
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
        await settle_succeeded_topup(session, payment=payment, source=source, bot=bot)
    logging.getLogger("simulation.topup").info(
        "💰 [TOPUP REFRESH] Succeeded and credited %s RUB to user %s",
        payment.amount,
        payment.user_id,
    )
    return payment


# Apply monkeypatches to external service clients
AmneziaClient.create_user_result = mock_amnezia_create_user_result
AmneziaClient.delete_user_result = mock_amnezia_delete_user_result
AmneziaClient.update_client_result = mock_amnezia_update_client_result
AmneziaClient.get_all_clients = mock_amnezia_get_all_clients
AmneziaClient.healthcheck = mock_amnezia_healthcheck
AmneziaClient.get_server_load = mock_amnezia_get_server_load
AmneziaClient.get_server_info = mock_amnezia_get_server_info

XrayNodeClient.check_health = mock_xray_check_health
XrayNodeClient.sync_client = mock_xray_sync_client
XrayNodeClient.get_inventory = mock_xray_get_inventory
XrayNodeClient.remove_client = mock_xray_remove_client
XrayNodeClient.get_traffic_snapshot = mock_xray_get_traffic_snapshot

YooKassaService.create_payment_result = classmethod(mock_yookassa_create_payment_result)
YooKassaService.get_payment_result = classmethod(mock_yookassa_get_payment_result)
topup_refresh.request_topup_status_refresh = mock_request_topup_status_refresh
account_topup.request_topup_status_refresh = mock_request_topup_status_refresh

import bot.handlers.payment.balance_routes as balance_routes
balance_routes.request_topup_status_refresh = mock_request_topup_status_refresh

import services.workers.payments as payments_worker

async def mock_recover_stale_topups(bot):
    return 0

payments_worker._recover_stale_topups = mock_recover_stale_topups

# Simulation payment link router for instant checkout confirmation
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message
import re

sim_pay_router = Router(name="sim_pay_router")

@sim_pay_router.message(Command("start"), F.text.regexp(r"^/start\s+(pay_(?:test_)?([a-zA-Z0-9_-]+))$"))
async def handle_sim_payment_link(
    message: Message,
    session: AsyncSession,
    bot: Bot,
    db_user: User | None = None,
):
    try:
        await message.delete()
    except Exception:
        pass

    match = re.search(r"^/start\s+(pay_(?:test_)?([a-zA-Z0-9_-]+))$", message.text or "")
    if not match:
        return
    raw_id = match.group(2)

    from sqlalchemy import or_

    stmt = (
        select(Payment)
        .where(
            or_(
                Payment.external_id == f"mock_pay_{raw_id}",
                Payment.external_id == raw_id,
                Payment.public_order_id == raw_id,
                Payment.public_order_id == f"order_{raw_id}",
            )
        )
        .order_by(Payment.id.desc())
        .limit(1)
        .with_for_update()
    )
    payment = await session.scalar(stmt)
    if payment is None and db_user:
        payment = await session.scalar(
            select(Payment)
            .where(
                Payment.user_id == db_user.id,
                Payment.provider_status.in_(("pending", "waiting_for_capture", "creating")),
            )
            .order_by(Payment.id.desc())
            .limit(1)
            .with_for_update()
        )

    if payment:
        payment.provider_status = "succeeded"
        payment.provider_confirmed_at = now_utc()
        payment.paid_at = now_utc()
        payment.checkout_status = "completed"
        if payment.fulfillment_status not in {"succeeded", "reversed", "manual_review"}:
            await settle_succeeded_topup(session, payment=payment, source="sim_pay_link", bot=bot)
        await session.commit()

        logging.getLogger("simulation.yookassa").info(
            "🎉 [MOCK YOOKASSA] Payment #%s (%s RUB) successfully settled via start-link for user %s",
            payment.id, payment.amount, payment.user_id,
        )

        ctx = payment.topup_context or {}
        from bot.handlers.start import _build_hub_text_and_kb
        user_to_render = db_user or await session.scalar(select(User).where(User.id == payment.user_id))
        text_hub, kb_hub = await _build_hub_text_and_kb(session, user_to_render)

        if ctx.get("source") == "white_internet":
            notice = "✅ <b>Оплата тарифа «Белый Интернет» успешно подтверждена!</b>"
        else:
            notice = f"✅ <b>Тестовая оплата на сумму {int(payment.amount)} ₽ успешно подтверждена!</b>\nБаланс пополнен."

        from utils.telegram import EFFECT_CONFETTI, render_hub

        await render_hub(
            bot,
            message.chat.id,
            f"{notice}\n\n{text_hub}",
            kb_hub,
            session=session,
            trigger_message_id=message.message_id,
            message_effect_id=EFFECT_CONFETTI,
            parse_mode="HTML",
        )
    else:
        await message.answer("⚠️ Платеж не найден или уже был обработан.", parse_mode="HTML")


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
            # Find default 5-device 30-day tariff (or fallback to first active tariff)
            tariff = await session.scalar(
                select(Tariff).where(
                    Tariff.is_active.is_(True),
                    Tariff.device_limit == 5,
                    Tariff.duration_days == 30,
                ).limit(1)
            )
            if not tariff:
                tariff = await session.scalar(
                    select(Tariff).where(Tariff.is_active.is_(True)).order_by(Tariff.id.asc()).limit(1)
                )
            tariff_id = tariff.id if tariff else None
            tv = await session.scalar(
                select(TariffVersion).where(TariffVersion.tariff_id == tariff_id).order_by(TariffVersion.id.desc()).limit(1)
            ) if tariff_id else None
            tv_id = tv.id if tv else None

            if db_user:
                # Check for and heal legacy inconsistent seed data if present
                legacy_pvl = await session.scalar(
                    select(PaidValueLedgerEntry)
                    .where(
                        PaidValueLedgerEntry.user_id == db_user.id,
                        PaidValueLedgerEntry.entry_type == "account_purchase",
                    )
                    .order_by(PaidValueLedgerEntry.id.asc())
                    .limit(1)
                )
                if legacy_pvl and tv:
                    curr_tv = await session.get(TariffVersion, legacy_pvl.tariff_version_id)
                    if not curr_tv or curr_tv.duration_hours != legacy_pvl.paid_hours_delta or curr_tv.price_rub != legacy_pvl.paid_value_rub_delta:
                        legacy_pvl.tariff_version_id = tv.id
                        legacy_pvl.paid_hours_delta = tv.duration_hours
                        legacy_pvl.paid_value_rub_delta = tv.price_rub
                        legacy_ent = await session.scalar(
                            select(EntitlementEntry).where(
                                EntitlementEntry.beneficiary_user_id == db_user.id,
                                EntitlementEntry.source_type == "quote",
                                EntitlementEntry.source_id == str(legacy_pvl.quote_id),
                            )
                        )
                        if legacy_ent:
                            legacy_ent.hours_delta = tv.duration_hours
                            legacy_ent.days_delta = tv.duration_hours // 24
                            legacy_ent.tariff_id_snapshot = tariff_id
                            legacy_ent.device_limit_snapshot = tv.device_limit
                        legacy_q = await session.get(TariffQuote, legacy_pvl.quote_id)
                        if legacy_q:
                            legacy_q.target_tariff_version_id = tv.id
                            legacy_q.amount_due_rub = tv.price_rub
                            legacy_q.resulting_paid_hours = tv.duration_hours
                            legacy_q.resulting_paid_value_rub = tv.price_rub
                        db_user.current_tariff_id = tariff_id
                        db_user.device_limit = tv.device_limit
                        base_time = legacy_ent.created_at if legacy_ent else (db_user.created_at or now_utc())
                        db_user.subscription_end = base_time + timedelta(hours=tv.duration_hours)
                        await session.flush()
            else:
                    server = await session.scalar(
                        select(Server).where(Server.is_active.is_(True)).order_by(Server.id.asc()).limit(1)
                    )
                    server_id = server.id if server else 1

                    seed_duration_hours = tv.duration_hours if tv else 720
                    seed_price_rub = tv.price_rub if tv else Decimal(180)
                    seed_device_limit = tv.device_limit if tv else 5
                    seed_created_at = now_utc() - timedelta(days=2)
                    seed_sub_end = seed_created_at + timedelta(hours=seed_duration_hours)

                    # Create user record
                    db_user = User(
                        telegram_id=user.id,
                        username=user.username or f"user_{user.id}",
                        first_name=user.first_name or "Tester",
                        device_limit=seed_device_limit,
                        current_tariff_id=tariff_id,
                        subscription_end=seed_sub_end,
                        created_at=seed_created_at,
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
                        created_at=seed_created_at,
                        paid_at=seed_created_at,
                        credited_at=seed_created_at,
                        credit_notified_at=seed_created_at,
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
                        created_at=seed_created_at,
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
                        created_at=seed_created_at,
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
                        amount_due_rub=seed_price_rub,
                        resulting_paid_hours=seed_duration_hours,
                        resulting_paid_value_rub=seed_price_rub,
                        resulting_bonus_hours=0,
                        rounding_loss_hours=Decimal(0),
                        rounding_loss_value_rub=Decimal(0),
                        status="consumed",
                        consumed_at=seed_created_at,
                        purchase_notified_at=seed_created_at,
                        expires_at=seed_sub_end,
                        created_at=seed_created_at,
                    )
                    session.add(init_quote)
                    await session.flush()

                    init_ent = EntitlementEntry(
                        beneficiary_user_id=db_user.id,
                        source_type="quote",
                        source_id=str(init_quote.id),
                        entry_type="account_purchase_grant",
                        days_delta=seed_duration_hours // 24,
                        hours_delta=seed_duration_hours,
                        device_limit_snapshot=seed_device_limit,
                        tariff_id_snapshot=tariff_id,
                        created_at=seed_created_at,
                    )
                    init_pvl = PaidValueLedgerEntry(
                        user_id=db_user.id,
                        source_type="quote",
                        source_id=str(init_quote.id),
                        entry_type="account_purchase",
                        quote_id=init_quote.id,
                        paid_hours_delta=seed_duration_hours,
                        paid_value_rub_delta=seed_price_rub,
                        currency="RUB",
                        tariff_version_id=tv_id,
                        created_at=seed_created_at,
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


class AsyncRLock:
    """A task-aware re-entrant asyncio lock to prevent concurrent sessions on single-connection SQLite."""

    def __init__(self):
        self._lock = asyncio.Lock()
        self._owner: asyncio.Task | None = None
        self._count = 0

    async def acquire(self):
        current_task = asyncio.current_task()
        if self._owner == current_task:
            self._count += 1
            return
        await self._lock.acquire()
        self._owner = current_task
        self._count = 1

    def release(self):
        current_task = asyncio.current_task()
        if self._owner != current_task:
            raise RuntimeError("Cannot release lock owned by another task")
        self._count -= 1
        if self._count == 0:
            self._owner = None
            self._lock.release()

    async def __aenter__(self):
        await self.acquire()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.release()


# --- 5. MAIN SIMULATION RUNNER ---

async def run_simulation(args: argparse.Namespace):
    log_file = PROJECT_ROOT / "sim_bot.log"
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s - %(levelname)s - %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file, mode="a", encoding="utf-8"),
        ],
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
            connect_args={"check_same_thread": False, "isolation_level": None},
        )
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        db_conn._engine = engine
        db_conn._sessionmaker = session_factory

        # Protect single-connection SQLite with an AsyncRLock so concurrent background workers
        # cannot execute COMMIT while a request handler is executing inside a SAVEPOINT.
        _sqlite_task_lock = AsyncRLock()

        @asynccontextmanager
        async def _safe_sqlite_session_scope():
            async with _sqlite_task_lock:
                session = await db_conn.get_session()
                try:
                    yield session
                    await session.commit()
                    await db_conn._run_post_commit_tasks(session)
                except (Exception, asyncio.CancelledError):
                    await session.rollback()
                    session.info.pop("post_commit_tasks", None)
                    raise
                finally:
                    await session.close()

        db_conn._session_scope_override = _safe_sqlite_session_scope

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
            # Ensure unique indexes required by ON CONFLICT clauses
            await conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_api_operations_idempotency_key "
                    "ON api_operations (idempotency_key)"
                )
            )
            await conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_payments_external_id_not_null "
                    "ON payments (external_id) WHERE external_id IS NOT NULL"
                )
            )
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
            await conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_white_internet_live_user "
                    "ON white_internet_subscriptions (user_id) WHERE status IN ('PENDING', 'ACTIVE', 'EXHAUSTED')"
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

        # Seed White Internet baseline tariff & snapshot
        wl_tariff = await WhiteInternetService.get_or_create_white_internet_tariff(session)
        await get_or_create_current_version(session, wl_tariff)
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
                health_state=ServerHealthState.ONLINE,
                lifecycle_status=ServerLifecycleStatus.ACTIVE,
                max_clients=100,
                capabilities=[],
                extra_data={},
            ),
            Server(
                id=2,
                name="Германия #1 (Франкфурт)",
                country_flag="🇩🇪",
                api_url="http://de1.just1k.net:8080",
                api_key="enc_key_de",
                protocol=AMNEZIA_PROTOCOL,
                is_active=True,
                health_state=ServerHealthState.ONLINE,
                lifecycle_status=ServerLifecycleStatus.ACTIVE,
                max_clients=100,
                capabilities=[],
                extra_data={},
            ),
            Server(
                id=3,
                name="Швеция #1 (Стокгольм)",
                country_flag="🇸🇪",
                api_url="http://se1.just1k.net:8080",
                api_key="enc_key_se",
                protocol=AMNEZIA_PROTOCOL,
                is_active=True,
                health_state=ServerHealthState.ONLINE,
                lifecycle_status=ServerLifecycleStatus.ACTIVE,
                max_clients=100,
                capabilities=[],
                extra_data={},
            ),
            Server(
                id=4,
                name="Финляндия #1 (Хельсинки)",
                country_flag="🇫🇮",
                api_url="http://fi1.just1k.net:8080",
                api_key="enc_key_fi",
                protocol=AMNEZIA_PROTOCOL,
                is_active=True,
                health_state=ServerHealthState.ONLINE,
                lifecycle_status=ServerLifecycleStatus.ACTIVE,
                max_clients=100,
                capabilities=[],
                extra_data={},
            ),
            Server(
                id=10,
                name="Белый Интернет (Оригин МСК)",
                country_flag="🇷🇺",
                api_url="http://xray1.just1k.net:8444",
                api_key="xray_sim_key_123",
                protocol=XRAY_PROTOCOL,
                is_active=True,
                health_state=ServerHealthState.ONLINE,
                lifecycle_status=ServerLifecycleStatus.ACTIVE,
                max_clients=500,
                capabilities=["xray_origin"],
                xray_instance_epoch="sim_epoch_1",
                xray_instance_boot_id="sim_boot_1",
                xray_instance_starttime=1700000000,
                extra_data={
                    "cdn_domain": "cdn.yandex.net",
                    "relays": [
                        {
                            "id": 101,
                            "name": "Relay #1 (МСК)",
                            "host": "relay1.just1k.net",
                            "port": 443,
                            "code": "msk",
                        }
                    ],
                },
            ),
        ]
        for s in servers:
            existing_s = await session.scalar(
                select(Server.id).where(
                    (Server.id == s.id) | (Server.api_url == s.api_url)
                )
            )
            if not existing_s:
                session.add(s)
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
    global _SIMULATED_BOT_USERNAME
    _SIMULATED_BOT_USERNAME = me.username or "just1kbot"
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
    from bot.handlers.white_internet import router as white_internet_router
    from integrations import get_all_bot_routers

    integration_routers = get_all_bot_routers()

    for r in [
        sim_pay_router,
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


def _ensure_single_instance():
    try:
        import psutil
        current_pid = os.getpid()
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                if proc.pid == current_pid:
                    continue
                cmdline = proc.info.get("cmdline") or []
                cmd_str = " ".join(cmdline).lower()
                if "simulate_bot.py" in cmd_str:
                    logging.getLogger("simulation").warning(
                        "🛑 [SINGLE INSTANCE] Terminating duplicate simulation process PID %s...",
                        proc.pid,
                    )
                    proc.terminate()
                    try:
                        proc.wait(timeout=3)
                    except psutil.TimeoutExpired:
                        proc.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    except Exception as e:
        logging.getLogger("simulation").debug("Single instance check error: %s", e)


def main():
    _ensure_single_instance()
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
