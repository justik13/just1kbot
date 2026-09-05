"""Tests for White Internet integration with admin panel and BanService.

Covers:
1. BanService deactivating WhiteInternetSubscription to DISABLED / PENDING_DELETE and revoking on Xray node.
2. User card rendering with White Internet details (status badge, traffic quota, expiry, server).
3. Admin trial reset confirmation and apply flows.
4. Admin trial grant flow.
5. Admin dashboard White Internet metrics.
6. Ban protection in create_trial_subscription.
"""

from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.types import CallbackQuery, Message, User as TgUser
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.constants import AdminAuditAction
from bot.handlers.admin.dashboard import _show_admin_dashboard
from bot.handlers.admin.users.common import (
    _get_white_internet_card_info,
    format_user_card_text,
)
from bot.handlers.admin.users.subscription_menu_routes import (
    admin_wl_grant_trial,
    admin_wl_reset_apply,
    admin_wl_reset_confirm,
)
from bot.keyboards.admin.users import get_admin_subscription_keyboard
from config.enums import (
    WhiteInternetProvisioningStatus,
    WhiteInternetStatus,
)
from database.models import Server, User, WhiteInternetSubscription
from database.repositories import white_internet_repo
from services.ban_service import BanService, BanStatus
from services.white_internet_service import WhiteInternetService
from utils.datetime_helpers import now_utc


class TestBanServiceWhiteInternet(unittest.IsolatedAsyncioTestCase):
    """Test suite for BanService interactions with White Internet."""

    async def asyncSetUp(self):
        self.session = AsyncMock(spec=AsyncSession)
        self.admin_id = 99999
        self.user = User(
            id=42,
            telegram_id=123456789,
            first_name="TestUser",
            is_banned=False,
            is_deleted=False,
        )
        self.origin_server = Server(
            id=1,
            name="Origin-RU",
            api_url="https://origin.example.test:8444",
            api_key="secret-api-key",
            capabilities=["xray_origin"],
        )

    async def test_ban_user_deactivates_white_internet_subscription(self):
        """When user is banned, White Internet subscriptions must be set to DISABLED / PENDING_DELETE."""
        sub = WhiteInternetSubscription(
            id=10,
            user_id=self.user.id,
            origin_node_id=self.origin_server.id,
            token="test-token-12345",
            uuid="test-uuid-12345",
            status=WhiteInternetStatus.ACTIVE,
            provisioning_status=WhiteInternetProvisioningStatus.ACTIVE,
            desired_version=1,
            actual_version=1,
            expires_at=now_utc() + timedelta(days=3),
        )

        mock_scalars = AsyncMock()
        mock_scalars_res = MagicMock()
        mock_scalars_res.all.return_value = []
        mock_scalars.return_value = mock_scalars_res

        with patch("services.ban_service.get_user_by_telegram_id", new=AsyncMock(return_value=self.user)), \
             patch.object(self.session, "scalar", new=AsyncMock(return_value=self.user)), \
             patch.object(self.session, "scalars", new=mock_scalars), \
             patch("services.ban_service.update_user", new=AsyncMock()) as mock_update_user, \
             patch("services.ban_service.ProfileDeletionService.delete_profiles_for_user", new=AsyncMock(return_value=1)), \
             patch("services.ban_service.AuditService.log_action", new=AsyncMock()) as mock_audit, \
             patch("services.ban_service.invalidate_user_cache"), \
             patch.object(WhiteInternetService, "deactivate_user_subscriptions", new=AsyncMock(return_value=[sub])) as mock_deactivate:

            ok, status = await BanService.ban_user(
                self.session,
                admin_id=self.admin_id,
                telegram_id=self.user.telegram_id,
            )

            self.assertTrue(ok)
            self.assertEqual(status, BanStatus.BANNED)
            mock_update_user.assert_awaited_once_with(self.session, self.user, is_banned=True)
            mock_deactivate.assert_awaited_once_with(
                self.session,
                self.user.id,
                reason="user_banned",
            )
            mock_audit.assert_awaited_once()
            audit_call_kwargs = mock_audit.call_args[1]
            self.assertEqual(audit_call_kwargs["action"], AdminAuditAction.BAN_USER)
            self.assertEqual(audit_call_kwargs["details"]["white_internet_disabled"], 1)

    async def test_white_internet_service_deactivate_method(self):
        """WhiteInternetService.deactivate_user_subscriptions sets DISABLED and PENDING_DELETE and schedules node deprovision."""
        sub = WhiteInternetSubscription(
            id=10,
            user_id=self.user.id,
            origin_node_id=self.origin_server.id,
            token="test-token-12345",
            uuid="test-uuid-12345",
            status=WhiteInternetStatus.ACTIVE,
            provisioning_status=WhiteInternetProvisioningStatus.ACTIVE,
            desired_version=1,
            actual_version=1,
            expires_at=now_utc() + timedelta(days=3),
        )

        mock_execute = MagicMock()
        mock_execute.scalars.return_value.all.return_value = [sub]
        self.session.execute = AsyncMock(return_value=mock_execute)
        self.session.get = AsyncMock(return_value=self.origin_server)

        with patch("services.white_internet_service._deprovision_old_node_safe", new_callable=AsyncMock) as mock_deprovision:
            deactivated = await WhiteInternetService.deactivate_user_subscriptions(
                self.session,
                self.user.id,
                reason="user_banned",
            )

            self.assertEqual(len(deactivated), 1)
            self.assertEqual(sub.status, WhiteInternetStatus.DISABLED)
            self.assertEqual(sub.status_reason, "user_banned")
            self.assertEqual(sub.provisioning_status, WhiteInternetProvisioningStatus.PENDING_DELETE)
            self.assertEqual(sub.desired_version, 2)
            self.session.flush.assert_awaited_once()
            mock_deprovision.assert_called_once()


class TestAdminUserCardWhiteInternet(unittest.IsolatedAsyncioTestCase):
    """Test suite for user card rendering with White Internet information."""

    async def asyncSetUp(self):
        self.session = AsyncMock(spec=AsyncSession)
        self.user = User(
            id=42,
            telegram_id=123456789,
            username="test_admin_user",
            first_name="AdminCardUser",
            is_banned=False,
            created_at=datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
        )
        self.server = Server(id=5, name="Node-Frankfurt")

    async def test_user_card_renders_white_internet_block_when_present(self):
        """User card must include status, quota/traffic, expiry, and server when White Internet exists."""
        sub = WhiteInternetSubscription(
            id=1,
            user_id=self.user.id,
            origin_node_id=5,
            status=WhiteInternetStatus.ACTIVE,
            base_traffic_bytes=10 * 1024 * 1024 * 1024,
            extra_traffic_bytes=0,
            traffic_used_bytes=2 * 1024 * 1024 * 1024,
            expires_at=datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc),
        )

        with patch("database.repositories.white_internet_repo.get_subscription_by_user_id", new=AsyncMock(return_value=sub)):
            self.session.get.return_value = self.server

            card_info = await _get_white_internet_card_info(self.session, self.user.id)
            self.assertIsNotNone(card_info)
            self.assertIn("Белый Интернет", card_info)
            self.assertIn("ACTIVE", card_info)
            self.assertIn("2.0 GiB / 10.0 GiB", card_info)
            self.assertIn("Node-Frankfurt", card_info)

            full_card = format_user_card_text(
                self.user,
                profiles=[],
                referrals=[],
                now=now_utc(),
                white_internet_info=card_info,
            )
            self.assertIn("Белый Интернет", full_card)
            self.assertIn("Node-Frankfurt", full_card)

    async def test_user_card_omits_white_internet_block_when_absent(self):
        """User card must omit White Internet block if user has no subscription."""
        with patch("database.repositories.white_internet_repo.get_subscription_by_user_id", new=AsyncMock(return_value=None)):
            card_info = await _get_white_internet_card_info(self.session, self.user.id)
            self.assertIsNone(card_info)

            full_card = format_user_card_text(
                self.user,
                profiles=[],
                referrals=[],
                now=now_utc(),
                white_internet_info=None,
            )
            self.assertNotIn("Белый Интернет", full_card)


class TestAdminSubscriptionMenuWhiteInternet(unittest.IsolatedAsyncioTestCase):
    """Test suite for White Internet trial management in admin subscription menu."""

    async def asyncSetUp(self):
        self.session = AsyncMock(spec=AsyncSession)
        self.user = User(
            id=42,
            telegram_id=123456789,
            username="wl_user",
            first_name="WlUser",
            is_banned=False,
        )

    def test_subscription_keyboard_wl_buttons(self):
        """Keyboard must render reset button when user has WL sub, and grant button when no active WL sub."""
        # 1. Has WL sub, active -> has reset button, no grant button
        kb_active = get_admin_subscription_keyboard(
            telegram_id=self.user.telegram_id,
            has_active_sub=False,
            has_wl_sub=True,
            wl_is_active=True,
        )
        buttons_active = [btn.callback_data for row in kb_active.inline_keyboard for btn in row]
        self.assertIn(f"admin_wl_reset_confirm:{self.user.telegram_id}", buttons_active)
        self.assertNotIn(f"admin_wl_grant_trial:{self.user.telegram_id}", buttons_active)

        # 2. Has WL sub, but expired/inactive -> has reset button (grant shown only if not has_wl_sub)
        kb_expired = get_admin_subscription_keyboard(
            telegram_id=self.user.telegram_id,
            has_active_sub=False,
            has_wl_sub=True,
            wl_is_active=False,
        )
        buttons_expired = [btn.callback_data for row in kb_expired.inline_keyboard for btn in row]
        self.assertIn(f"admin_wl_reset_confirm:{self.user.telegram_id}", buttons_expired)

        # 3. No WL sub at all -> no reset button, has grant button
        kb_none = get_admin_subscription_keyboard(
            telegram_id=self.user.telegram_id,
            has_active_sub=False,
            has_wl_sub=False,
            wl_is_active=False,
        )
        buttons_none = [btn.callback_data for row in kb_none.inline_keyboard for btn in row]
        self.assertNotIn(f"admin_wl_reset_confirm:{self.user.telegram_id}", buttons_none)
        self.assertIn(f"admin_wl_grant_trial:{self.user.telegram_id}", buttons_none)

    async def test_admin_wl_reset_confirm_callback(self):
        """admin_wl_reset_confirm displays confirmation prompt."""
        callback = MagicMock(spec=CallbackQuery)
        callback.from_user = TgUser(id=123456789, is_bot=False, first_name="Admin")
        callback.data = f"admin_wl_reset_confirm:{self.user.telegram_id}"
        callback.message = MagicMock(spec=Message)
        callback.message.edit_text = AsyncMock()
        callback.answer = AsyncMock()

        with patch("bot.handlers.admin.users.subscription_menu_routes.is_admin", return_value=True):
            await admin_wl_reset_confirm(callback, self.session)

            callback.message.edit_text.assert_awaited_once()
            call_kwargs = callback.message.edit_text.call_args[1]
            self.assertIn("Сброс триала", call_kwargs.get("text", callback.message.edit_text.call_args[0][0]))

    async def test_admin_wl_reset_apply_callback(self):
        """admin_wl_reset_apply calls reset_user_trial, logs audit, and refreshes menu."""
        callback = MagicMock(spec=CallbackQuery)
        callback.from_user = TgUser(id=123456789, is_bot=False, first_name="Admin")
        callback.data = f"admin_wl_reset_apply:{self.user.telegram_id}"
        callback.message = MagicMock(spec=Message)
        callback.message.edit_text = AsyncMock()
        callback.answer = AsyncMock()

        with patch("bot.handlers.admin.users.subscription_menu_routes.is_admin", return_value=True), \
             patch("bot.handlers.admin.users.subscription_menu_routes.get_user_by_telegram_id", new=AsyncMock(return_value=self.user)), \
             patch.object(WhiteInternetService, "reset_user_trial", new=AsyncMock(return_value=(True, "ok"))) as mock_reset, \
             patch("bot.handlers.admin.users.subscription_menu_routes.AuditService.log_action", new=AsyncMock()) as mock_audit, \
             patch("bot.handlers.admin.users.subscription_menu_routes.admin_subscription_menu", new=AsyncMock()) as mock_menu:

            await admin_wl_reset_apply(callback, self.session)

            mock_reset.assert_awaited_once_with(self.session, self.user.id)
            mock_audit.assert_awaited_once_with(
                self.session,
                admin_id=callback.from_user.id,
                action=AdminAuditAction.WHITE_INTERNET_RESET_TRIAL,
                target_type="user",
                target_id=self.user.id,
                details={"telegram_id": self.user.telegram_id},
            )
            callback.answer.assert_awaited_with(texts.ADMIN_WL_RESET_SUCCESS, show_alert=True)
            mock_menu.assert_awaited_once()

    async def test_admin_wl_grant_trial_callback(self):
        """admin_wl_grant_trial calls create_trial_subscription, logs audit, and refreshes menu."""
        callback = MagicMock(spec=CallbackQuery)
        callback.from_user = TgUser(id=123456789, is_bot=False, first_name="Admin")
        callback.data = f"admin_wl_grant_trial:{self.user.telegram_id}"
        callback.message = MagicMock(spec=Message)
        callback.message.edit_text = AsyncMock()
        callback.answer = AsyncMock()

        sub = WhiteInternetSubscription(id=99, user_id=self.user.id)

        with patch("bot.handlers.admin.users.subscription_menu_routes.is_admin", return_value=True), \
             patch("bot.handlers.admin.users.subscription_menu_routes.get_user_by_telegram_id", new=AsyncMock(return_value=self.user)), \
             patch.object(WhiteInternetService, "create_trial_subscription", new=AsyncMock(return_value=(True, "ok", sub))) as mock_create, \
             patch("bot.handlers.admin.users.subscription_menu_routes.AuditService.log_action", new=AsyncMock()) as mock_audit, \
             patch("bot.handlers.admin.users.subscription_menu_routes.admin_subscription_menu", new=AsyncMock()) as mock_menu:

            await admin_wl_grant_trial(callback, self.session)

            mock_create.assert_awaited_once_with(self.session, self.user.id)
            mock_audit.assert_awaited_once_with(
                self.session,
                admin_id=callback.from_user.id,
                action=AdminAuditAction.WHITE_INTERNET_GRANT_TRIAL,
                target_type="user",
                target_id=self.user.id,
                details={"telegram_id": self.user.telegram_id, "subscription_id": 99},
            )
            callback.answer.assert_awaited_with(texts.ADMIN_WL_GRANT_SUCCESS, show_alert=True)
            mock_menu.assert_awaited_once()


class TestWhiteInternetDashboardMetrics(unittest.IsolatedAsyncioTestCase):
    """Test suite for White Internet metrics on the admin dashboard."""

    async def asyncSetUp(self):
        self.session = AsyncMock(spec=AsyncSession)

    async def test_dashboard_stats_query_calculation(self):
        """get_white_internet_dashboard_stats correctly extracts active count and traffic sum."""
        mock_row = MagicMock(active_count=4, total_traffic_bytes=10737418240)
        mock_exec = MagicMock()
        mock_exec.one.return_value = mock_row
        self.session.execute = AsyncMock(return_value=mock_exec)

        res = await white_internet_repo.get_white_internet_dashboard_stats(self.session)
        self.assertEqual(res["active_count"], 4)
        self.assertEqual(res["total_traffic_bytes"], 10737418240)

    async def test_dashboard_renders_white_internet_metric(self):
        """_show_admin_dashboard includes the White Internet active count and formatted traffic."""
        callback = MagicMock(spec=CallbackQuery)
        callback.message = MagicMock(spec=Message)
        callback.message.edit_text = AsyncMock()

        with patch("bot.handlers.admin.dashboard.get_dashboard_stats", new=AsyncMock(return_value={"total": 100, "active": 20, "new_24h": 5})), \
             patch("bot.handlers.admin.dashboard.get_white_internet_dashboard_stats", new=AsyncMock(return_value={"active_count": 7, "total_traffic_bytes": 53687091200})), \
             patch("bot.handlers.admin.dashboard.get_total_free_ips", new=AsyncMock(return_value=150)), \
             patch("bot.handlers.admin.dashboard._get_financial_stats", new=AsyncMock(return_value={"rev_24h": 1000, "count_24h": 2, "rev_7d": 5000, "rev_30d": 20000, "avg_check": 500})), \
             patch("bot.handlers.admin.dashboard._get_disputes_count", new=AsyncMock(return_value=0)), \
             patch("bot.handlers.admin.dashboard._get_dead_queues_count", new=AsyncMock(return_value=0)), \
             patch("bot.handlers.admin.dashboard._get_servers_capacity_summary", new=AsyncMock(return_value="🟢 Server-1")), \
             patch("bot.handlers.admin.dashboard.MaintenanceService.is_enabled", new=AsyncMock(return_value=False)):

            await _show_admin_dashboard(callback, self.session)

            callback.message.edit_text.assert_awaited_once()
            rendered_text = callback.message.edit_text.call_args[0][0]
            self.assertIn("Белый Интернет", rendered_text)
            self.assertIn("7", rendered_text)
            self.assertIn("50.0 GiB", rendered_text)


class TestWhiteInternetTrialProtectionAndReset(unittest.IsolatedAsyncioTestCase):
    """Test suite for WhiteInternetService trial reset and ban protection."""

    async def asyncSetUp(self):
        self.session = AsyncMock(spec=AsyncSession)

    async def test_banned_user_cannot_create_trial(self):
        """create_trial_subscription rejects banned user."""
        banned_user = User(id=1, telegram_id=555, is_banned=True, is_deleted=False)

        with patch("services.white_internet_service.lock_checkout_user", new=AsyncMock(return_value=banned_user)):
            ok, msg, sub = await WhiteInternetService.create_trial_subscription(self.session, banned_user.id)
            self.assertFalse(ok)
            self.assertIsNone(sub)

    async def test_reset_user_trial_deletes_subscriptions_and_deprovisions(self):
        """reset_user_trial deletes subscriptions and schedules deprovisioning on node."""
        user = User(id=1, telegram_id=555, is_banned=False, is_deleted=False)
        server = Server(id=2, name="Origin", api_url="https://node.example.test:8444", api_key="test-key")
        sub = WhiteInternetSubscription(
            id=10,
            user_id=user.id,
            origin_node_id=2,
            uuid="client-uuid-123",
            desired_version=1,
        )

        mock_exec = MagicMock()
        mock_exec.scalars.return_value.all.return_value = [sub]
        self.session.execute = AsyncMock(return_value=mock_exec)
        self.session.get = AsyncMock(return_value=server)

        with patch("services.white_internet_service.lock_checkout_user", new=AsyncMock(return_value=user)), \
             patch("services.white_internet_service._deprovision_old_node_safe", new_callable=AsyncMock) as mock_deprovision:

            ok, msg = await WhiteInternetService.reset_user_trial(self.session, user.id)
            self.assertTrue(ok)
            self.assertEqual(msg, texts.ADMIN_WL_RESET_SUCCESS)
            self.session.delete.assert_called_once_with(sub)
            self.session.flush.assert_awaited_once()
            mock_deprovision.assert_called_once()

    async def test_reset_user_trial_returns_error_when_no_subscriptions(self):
        """reset_user_trial returns WL_SUB_NOT_FOUND when user has no subscriptions."""
        user = User(id=1, telegram_id=555, is_banned=False, is_deleted=False)
        mock_exec = MagicMock()
        mock_exec.scalars.return_value.all.return_value = []
        self.session.execute = AsyncMock(return_value=mock_exec)

        with patch("services.white_internet_service.lock_checkout_user", new=AsyncMock(return_value=user)):
            ok, msg = await WhiteInternetService.reset_user_trial(self.session, user.id)
            self.assertFalse(ok)
            self.assertEqual(msg, texts.WL_SUB_NOT_FOUND)

    def test_audit_actions_contains_white_internet_actions(self):
        """AUDIT_ACTIONS dict in dashboard texts must contain White Internet audit labels."""
        from bot.texts.admin.dashboard import AUDIT_ACTIONS
        self.assertIn("WHITE_INTERNET_RESET_TRIAL", AUDIT_ACTIONS)
        self.assertIn("WHITE_INTERNET_GRANT_TRIAL", AUDIT_ACTIONS)
        self.assertIn("Сброс триала", AUDIT_ACTIONS["WHITE_INTERNET_RESET_TRIAL"])
        self.assertIn("Выдача триала", AUDIT_ACTIONS["WHITE_INTERNET_GRANT_TRIAL"])


if __name__ == "__main__":
    unittest.main()
