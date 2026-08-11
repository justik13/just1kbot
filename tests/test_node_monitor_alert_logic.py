import os

os.environ.setdefault("BOT_TOKEN", "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11")
os.environ.setdefault("ADMIN_IDS", "[100]")
os.environ.setdefault("SUPPORT_USERNAME", "test_support")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost/db")
os.environ.setdefault("DB_ENCRYPTION_KEY", "12345678901234567890123456789012")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("REDIS_PASSWORD", "testpass")
os.environ.setdefault("YOOKASSA_SHOP_ID", "12345")
os.environ.setdefault("YOOKASSA_SECRET_KEY", "test_key")
os.environ.setdefault("YOOKASSA_RETURN_URL", "https://t.me/test_bot?start={bot_username}")
os.environ.setdefault("YOOKASSA_WEBHOOK_PORT", "8080")
os.environ.setdefault("DOMAIN", "myrealdomain.com")
os.environ.setdefault("SSL_EMAIL", "admin@myrealdomain.com")

import time
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.types import CallbackQuery

from database.models import Server
from services.workers.node_monitor import (
    ServerHealthState,
    check_node_resources_and_alerts,
    get_server_monitor_state,
)
from bot.handlers.admin.servers.card_routes import dismiss_admin_alert


@pytest.fixture(autouse=True)
def clean_monitor_states():
    from services.workers.node_monitor import _server_states
    _server_states.clear()
    yield
    _server_states.clear()


@pytest.fixture(autouse=True)
def mock_env_settings():
    mock_settings = MagicMock()
    mock_settings.ADMIN_IDS = [100]
    with patch("services.workers.node_monitor.get_settings", return_value=mock_settings), \
         patch("config.settings.get_settings", return_value=mock_settings):
        yield mock_settings


@pytest.fixture(autouse=True)
def mock_db_session_scope():
    dummy_session = AsyncMock()
    @asynccontextmanager
    async def dummy_scope():
        yield dummy_session
    with patch("services.workers.node_monitor.session_scope", side_effect=dummy_scope):
        yield dummy_session


@pytest.fixture
def mock_bot():
    bot = AsyncMock()
    bot.send_message = AsyncMock()
    return bot


@pytest.mark.asyncio
async def test_healthy_server_no_alerts(mock_bot):
    server = MagicMock(spec=Server)
    server.id = 1
    server.name = "Germany-1"
    server.api_url = "https://vpn.example.com"
    server.api_key = "secret_key"
    server.is_active = True
    server.disabled_reason = None

    with patch("services.workers.node_monitor.get_all_servers", return_value=[server]), \
         patch("services.workers.node_monitor.AmneziaClient") as mock_client_cls, \
         patch("services.workers.node_monitor.get_server_by_id", return_value=server), \
         patch("services.workers.node_monitor.update_server"):

        client_instance = mock_client_cls.return_value
        client_instance.healthcheck = AsyncMock(return_value=True)
        client_instance.get_server_load = AsyncMock(return_value={"disk_percent": 30.0})

        await check_node_resources_and_alerts(mock_bot)

        mock_bot.send_message.assert_not_called()
        state = get_server_monitor_state(1)
        assert state.health_state == ServerHealthState.ONLINE
        assert state.consecutive_fails == 0


@pytest.mark.asyncio
async def test_transient_glitch_no_alert(mock_bot):
    """FAIL #1 followed by OK on 30s confirmation retry -> remains ONLINE, 0 alerts."""
    server = MagicMock(spec=Server)
    server.id = 2
    server.name = "Finland-1"
    server.api_url = "https://vpn2.example.com"
    server.api_key = "secret"
    server.is_active = True
    server.disabled_reason = None

    with patch("services.workers.node_monitor.get_all_servers", return_value=[server]), \
         patch("services.workers.node_monitor.AmneziaClient") as mock_client_cls, \
         patch("services.workers.node_monitor.get_server_by_id", return_value=server), \
         patch("services.workers.node_monitor.update_server"), \
         patch("services.workers.node_monitor.asyncio.sleep", new_callable=AsyncMock):

        client_instance = mock_client_cls.return_value
        # First call fails, 30s retry succeeds
        client_instance.healthcheck = AsyncMock(side_effect=[False, True])

        await check_node_resources_and_alerts(mock_bot)

        mock_bot.send_message.assert_not_called()
        state = get_server_monitor_state(2)
        assert state.health_state == ServerHealthState.ONLINE


@pytest.mark.asyncio
async def test_confirmed_failure_sends_warning_alert(mock_bot):
    """FAIL #1 + 30s retry FAIL #2 -> transitions to PROBLEM, sends 1 warning alert."""
    server = MagicMock(spec=Server)
    server.id = 3
    server.name = "US-1"
    server.api_url = "https://vpn3.example.com"
    server.api_key = "secret"
    server.is_active = True
    server.disabled_reason = None

    with patch("services.workers.node_monitor.get_all_servers", return_value=[server]), \
         patch("services.workers.node_monitor.AmneziaClient") as mock_client_cls, \
         patch("services.workers.node_monitor.asyncio.sleep", new_callable=AsyncMock):

        client_instance = mock_client_cls.return_value
        # Both checks fail
        client_instance.healthcheck = AsyncMock(return_value=False)

        await check_node_resources_and_alerts(mock_bot)

        assert mock_bot.send_message.call_count == 1
        call_args = mock_bot.send_message.call_args
        assert "Проблема с VPN-сервером" in call_args.kwargs["text"]
        assert call_args.kwargs["chat_id"] == 100

        # Verify buttons exist (card and dismiss)
        reply_markup = call_args.kwargs["reply_markup"]
        inline_buttons = [btn.callback_data for row in reply_markup.inline_keyboard for btn in row]
        assert "admin_server_card:3" in inline_buttons
        assert "admin_dismiss_alert" in inline_buttons

        state = get_server_monitor_state(3)
        assert state.health_state == ServerHealthState.PROBLEM


@pytest.mark.asyncio
async def test_problem_observation_window_silence_and_flapping_protection(mock_bot):
    """While in PROBLEM, repeated checks do not spam. 1 success followed by fail does NOT trigger recovery."""
    server = MagicMock(spec=Server)
    server.id = 4
    server.name = "UK-1"
    server.api_url = "https://vpn4.example.com"
    server.api_key = "secret"
    server.is_active = True
    server.disabled_reason = None

    st = get_server_monitor_state(4)
    st.health_state = ServerHealthState.PROBLEM
    st.problem_started_at = time.monotonic()

    with patch("services.workers.node_monitor.get_all_servers", return_value=[server]), \
         patch("services.workers.node_monitor.AmneziaClient") as mock_client_cls, \
         patch("services.workers.node_monitor.get_server_by_id", return_value=server), \
         patch("services.workers.node_monitor.update_server"):

        client_instance = mock_client_cls.return_value

        # Check 1: FAIL (should send no new alerts)
        client_instance.healthcheck = AsyncMock(return_value=False)
        await check_node_resources_and_alerts(mock_bot)
        mock_bot.send_message.assert_not_called()
        assert st.health_state == ServerHealthState.PROBLEM

        # Check 2: 1x OK (flapping protection: not enough successes yet)
        client_instance.healthcheck = AsyncMock(return_value=True)
        await check_node_resources_and_alerts(mock_bot)
        mock_bot.send_message.assert_not_called()
        assert st.consecutive_successes == 1
        assert st.health_state == ServerHealthState.PROBLEM

        # Check 3: FAIL again -> resets consecutive_successes to 0
        client_instance.healthcheck = AsyncMock(return_value=False)
        await check_node_resources_and_alerts(mock_bot)
        mock_bot.send_message.assert_not_called()
        assert st.consecutive_successes == 0
        assert st.health_state == ServerHealthState.PROBLEM


@pytest.mark.asyncio
async def test_problem_confirmed_recovery(mock_bot):
    """3 consecutive successes in PROBLEM -> transitions to ONLINE, sends 1 recovery alert."""
    server = MagicMock(spec=Server)
    server.id = 5
    server.name = "NL-1"
    server.api_url = "https://vpn5.example.com"
    server.api_key = "secret"
    server.is_active = True
    server.disabled_reason = None

    st = get_server_monitor_state(5)
    st.health_state = ServerHealthState.PROBLEM
    st.problem_started_at = time.monotonic()
    st.consecutive_successes = 2  # Already had 2 successes

    with patch("services.workers.node_monitor.get_all_servers", return_value=[server]), \
         patch("services.workers.node_monitor.AmneziaClient") as mock_client_cls, \
         patch("services.workers.node_monitor.get_server_by_id", return_value=server), \
         patch("services.workers.node_monitor.update_server"):

        client_instance = mock_client_cls.return_value
        client_instance.healthcheck = AsyncMock(return_value=True)

        await check_node_resources_and_alerts(mock_bot)

        assert mock_bot.send_message.call_count == 1
        text = mock_bot.send_message.call_args.kwargs["text"]
        assert "VPN-сервер восстановлен" in text
        assert st.health_state == ServerHealthState.ONLINE


@pytest.mark.asyncio
async def test_problem_timeout_escalates_to_auto_disabled(mock_bot):
    """If 15 minutes elapse in PROBLEM without recovery, transitions to AUTO_DISABLED and disables server in DB."""
    server = MagicMock(spec=Server)
    server.id = 6
    server.name = "DE-1"
    server.api_url = "https://vpn6.example.com"
    server.api_key = "secret"
    server.is_active = True
    server.disabled_reason = None

    st = get_server_monitor_state(6)
    st.health_state = ServerHealthState.PROBLEM
    # Simulate problem started 16 minutes ago
    st.problem_started_at = time.monotonic() - (16 * 60.0)

    db_server = MagicMock()

    with patch("services.workers.node_monitor.get_all_servers", return_value=[server]), \
         patch("services.workers.node_monitor.AmneziaClient") as mock_client_cls, \
         patch("services.workers.node_monitor.get_server_by_id", return_value=db_server), \
         patch("services.workers.node_monitor.update_server", new_callable=AsyncMock) as mock_update:

        client_instance = mock_client_cls.return_value
        client_instance.healthcheck = AsyncMock(return_value=False)

        await check_node_resources_and_alerts(mock_bot)

        assert mock_bot.send_message.call_count == 1
        text = mock_bot.send_message.call_args.kwargs["text"]
        assert "Сервер автоматически отключён" in text

        # Verify DB update was called with is_active=False and disabled_reason="AUTO_UNAVAILABLE"
        mock_update.assert_called_once()
        assert mock_update.call_args.kwargs["is_active"] is False
        assert mock_update.call_args.kwargs["disabled_reason"] == "AUTO_UNAVAILABLE"

        assert st.health_state == ServerHealthState.AUTO_DISABLED


@pytest.mark.asyncio
async def test_auto_disabled_quiet_polling_and_recovery_notification(mock_bot):
    """AUTO_DISABLED server polling is quiet. 3x OK sends 1 recovery notice but leaves is_active=False."""
    server = MagicMock(spec=Server)
    server.id = 7
    server.name = "FR-1"
    server.api_url = "https://vpn7.example.com"
    server.api_key = "secret"
    server.is_active = False
    server.disabled_reason = "AUTO_UNAVAILABLE"

    st = get_server_monitor_state(7)
    st.health_state = ServerHealthState.AUTO_DISABLED
    st.consecutive_successes = 2
    st.last_check_monotonic = time.monotonic() - (16 * 60.0)  # Ready for 15-min check

    with patch("services.workers.node_monitor.get_all_servers", return_value=[server]), \
         patch("services.workers.node_monitor.AmneziaClient") as mock_client_cls, \
         patch("services.workers.node_monitor.get_server_by_id", return_value=server), \
         patch("services.workers.node_monitor.update_server", new_callable=AsyncMock):

        client_instance = mock_client_cls.return_value
        client_instance.healthcheck = AsyncMock(return_value=True)

        await check_node_resources_and_alerts(mock_bot)

        assert mock_bot.send_message.call_count == 1
        text = mock_bot.send_message.call_args.kwargs["text"]
        assert "Сервер восстановлен" in text
        assert "Сервер остаётся отключённым" in text

        # Verify buttons include enable button and dismiss button
        reply_markup = mock_bot.send_message.call_args.kwargs["reply_markup"]
        inline_buttons = [btn.callback_data for row in reply_markup.inline_keyboard for btn in row]
        assert "admin_server_toggle:7" in inline_buttons
        assert "admin_dismiss_alert" in inline_buttons


@pytest.mark.asyncio
async def test_manual_disabled_server_completely_ignored(mock_bot):
    """MANUAL_DISABLED server sends 0 alerts and executes 0 auto-actions."""
    server = MagicMock(spec=Server)
    server.id = 8
    server.name = "JP-1"
    server.api_url = "https://vpn8.example.com"
    server.api_key = "secret"
    server.is_active = False
    server.disabled_reason = "MANUAL"

    with patch("services.workers.node_monitor.get_all_servers", return_value=[server]), \
         patch("services.workers.node_monitor.AmneziaClient") as mock_client_cls:

        await check_node_resources_and_alerts(mock_bot)

        mock_bot.send_message.assert_not_called()
        mock_client_cls.return_value.healthcheck.assert_not_called()


@pytest.mark.asyncio
async def test_dismiss_admin_alert_handler():
    """Callback admin_dismiss_alert deletes the alert message."""
    callback = AsyncMock(spec=CallbackQuery)
    callback.from_user = MagicMock(id=100)
    callback.answer = AsyncMock()
    callback.message = AsyncMock()

    with patch("bot.handlers.admin.servers.card_routes.is_admin", return_value=True):
        await dismiss_admin_alert(callback)

        callback.answer.assert_called_once_with("Удалено", show_alert=False)
        callback.message.delete.assert_called_once()
