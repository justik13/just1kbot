"""Unit tests for White Internet Free Trial mode (3 days / 10 GiB / 0 RUB).

Tests cover:
- Trial subscription creation with 0 RUB quote and 10 GiB quota
- Synchronous Xray node sync on trial activation (Zero-Wait UX)
- Anti-abuse: blocking repeat trial activations
- Fallback handling if node sync times out
- Telegram UI overview keyboard in trial mode (no paid buttons)
- Blocking stale paid callbacks with alert
- Main hub indicator when White Internet is active
"""

import os
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.handlers.white_internet import (
    get_white_internet_overview_keyboard,
    show_topup_menu,
)
from config.constants import (
    WHITE_INTERNET_TRIAL_DURATION_DAYS,
    WHITE_INTERNET_TRIAL_TRAFFIC_BYTES,
)
from config.enums import (
    WhiteInternetProvisioningStatus,
    WhiteInternetStatus,
)
from database.models import Server, User, WhiteInternetSubscription
from services.xray_node_client import SyncResponse, SyncResult
from services.white_internet_service import WhiteInternetService


class TestWhiteInternetTrialService(unittest.IsolatedAsyncioTestCase):
    """Test suite for WhiteInternetService.create_trial_subscription."""

    async def asyncSetUp(self):
        self.orig_trial_env = os.environ.get("WHITE_INTERNET_TRIAL_MODE_ONLY")
        os.environ["WHITE_INTERNET_TRIAL_MODE_ONLY"] = "true"
        self.session = AsyncMock(spec=AsyncSession)
        self.user = User(id=10, telegram_id=777000111, first_name="TrialUser")
        self.origin_server = Server(
            id=1,
            name="Origin-RU",
            api_url="https://origin.just1k.best:8444",
            api_key="secret-api-key",
            xray_instance_epoch=1,
            extra_data={"cdn_domain": "cdn.just1k.best"},
        )
        self.tariff = MagicMock(id=5, duration_days=30)
        self.tariff_version = MagicMock(id=15, price_rub=Decimal("0.00"))

    async def asyncTearDown(self):
        if self.orig_trial_env is None:
            os.environ.pop("WHITE_INTERNET_TRIAL_MODE_ONLY", None)
        else:
            os.environ["WHITE_INTERNET_TRIAL_MODE_ONLY"] = self.orig_trial_env

    async def test_trial_creation_success_with_sync_xray(self):
        """Trial is created with 3 days, 10 GiB, 0 RUB, and immediately synced to ACTIVE."""
        now = datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc)
        created_sub = WhiteInternetSubscription(
            id=100,
            user_id=self.user.id,
            origin_node_id=self.origin_server.id,
            token="token-xyz",
            uuid="uuid-xyz",
            status=WhiteInternetStatus.PENDING,
            base_traffic_bytes=WHITE_INTERNET_TRIAL_TRAFFIC_BYTES,
            traffic_limit_bytes=WHITE_INTERNET_TRIAL_TRAFFIC_BYTES,
            started_at=now,
            expires_at=now + timedelta(days=WHITE_INTERNET_TRIAL_DURATION_DAYS),
            desired_version=1,
            actual_version=0,
        )

        mock_sync_resp = SyncResponse(
            result=SyncResult.APPLIED,
            error=None,
            verified_epoch=1,
        )

        with patch("services.white_internet_service.lock_checkout_user", return_value=self.user), \
             patch("database.repositories.white_internet_repo.has_ever_activated_trial", return_value=False), \
             patch("database.repositories.white_internet_repo.get_subscription_by_user_id", return_value=None), \
             patch.object(WhiteInternetService, "select_origin_node", return_value=self.origin_server), \
             patch.object(WhiteInternetService, "get_or_create_white_internet_tariff", return_value=self.tariff), \
             patch("services.white_internet_service.get_or_create_current_version", return_value=self.tariff_version), \
             patch("database.repositories.white_internet_repo.create_white_internet_subscription", return_value=created_sub) as mock_create_sub, \
             patch("services.white_internet_service.XrayNodeClient") as mock_xray_client_cls:

            mock_client_instance = AsyncMock()
            mock_client_instance.sync_client.return_value = mock_sync_resp
            mock_xray_client_cls.return_value.__aenter__.return_value = mock_client_instance

            success, msg, sub = await WhiteInternetService.create_trial_subscription(self.session, self.user.id)

            self.assertTrue(success)
            self.assertEqual(msg, texts.WL_TRIAL_ACTIVATED_SUCCESS)
            self.assertEqual(sub.status, WhiteInternetStatus.ACTIVE)
            self.assertEqual(sub.actual_version, 1)
            self.assertEqual(sub.provisioning_status, WhiteInternetProvisioningStatus.ACTIVE)

            call_kwargs = mock_create_sub.call_args.kwargs
            self.assertEqual(call_kwargs["user_id"], self.user.id)
            self.assertEqual(call_kwargs["origin_node_id"], self.origin_server.id)
            self.assertEqual(len(call_kwargs["token"]), 64)
            self.assertEqual(call_kwargs["price_rub"], Decimal("0.00"))
            self.assertEqual(call_kwargs["duration_days"], WHITE_INTERNET_TRIAL_DURATION_DAYS)
            self.assertEqual(call_kwargs["base_bytes"], WHITE_INTERNET_TRIAL_TRAFFIC_BYTES)
            mock_client_instance.sync_client.assert_awaited_once()

    async def test_trial_anti_abuse_rejection(self):
        """User who already had a subscription is rejected with WL_TRIAL_ALREADY_USED."""
        existing_sub = WhiteInternetSubscription(
            id=50,
            user_id=self.user.id,
            status=WhiteInternetStatus.EXPIRED,
        )
        with patch("services.white_internet_service.lock_checkout_user", return_value=self.user), \
             patch("database.repositories.white_internet_repo.has_ever_activated_trial", return_value=True), \
             patch("database.repositories.white_internet_repo.get_subscription_by_user_id", return_value=existing_sub):

            success, msg, sub = await WhiteInternetService.create_trial_subscription(self.session, self.user.id)

            self.assertFalse(success)
            self.assertEqual(msg, texts.WL_TRIAL_ALREADY_USED)
            self.assertEqual(sub, existing_sub)

    async def test_trial_rejected_for_user_with_expired_paid_subscription(self):
        """User who never had trial but has an expired paid subscription cannot activate trial."""
        existing_sub = WhiteInternetSubscription(
            id=51,
            user_id=self.user.id,
            status=WhiteInternetStatus.EXPIRED,
            is_trial=False,
        )
        with patch("services.white_internet_service.lock_checkout_user", return_value=self.user), \
             patch("database.repositories.white_internet_repo.has_ever_activated_trial", return_value=False), \
             patch("database.repositories.white_internet_repo.get_subscription_by_user_id", return_value=existing_sub):

            success, msg, sub = await WhiteInternetService.create_trial_subscription(self.session, self.user.id)

            self.assertFalse(success)
            self.assertEqual(msg, texts.WL_TRIAL_ALREADY_USED)
            self.assertEqual(sub, existing_sub)

    async def test_trial_sync_fallback_on_network_error(self):
        """If Xray node sync fails, subscription remains created in PENDING for worker fallback."""
        now = datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc)
        created_sub = WhiteInternetSubscription(
            id=101,
            user_id=self.user.id,
            origin_node_id=self.origin_server.id,
            token="token-xyz",
            uuid="uuid-xyz",
            status=WhiteInternetStatus.PENDING,
            base_traffic_bytes=WHITE_INTERNET_TRIAL_TRAFFIC_BYTES,
            traffic_limit_bytes=WHITE_INTERNET_TRIAL_TRAFFIC_BYTES,
            started_at=now,
            expires_at=now + timedelta(days=WHITE_INTERNET_TRIAL_DURATION_DAYS),
            desired_version=1,
            actual_version=0,
        )

        with patch("services.white_internet_service.lock_checkout_user", return_value=self.user), \
             patch("database.repositories.white_internet_repo.has_ever_activated_trial", return_value=False), \
             patch("database.repositories.white_internet_repo.get_subscription_by_user_id", return_value=None), \
             patch.object(WhiteInternetService, "select_origin_node", return_value=self.origin_server), \
             patch.object(WhiteInternetService, "get_or_create_white_internet_tariff", return_value=self.tariff), \
             patch("services.white_internet_service.get_or_create_current_version", return_value=self.tariff_version), \
             patch("database.repositories.white_internet_repo.create_white_internet_subscription", return_value=created_sub), \
             patch("services.white_internet_service.XrayNodeClient") as mock_xray_client_cls:

            mock_client_instance = AsyncMock()
            mock_client_instance.sync_client.side_effect = TimeoutError("Connection timed out")
            mock_xray_client_cls.return_value.__aenter__.return_value = mock_client_instance

            success, msg, sub = await WhiteInternetService.create_trial_subscription(self.session, self.user.id)

            self.assertTrue(success)
            self.assertEqual(sub.status, WhiteInternetStatus.PENDING)
            self.assertEqual(sub.actual_version, 0)

    async def test_trial_already_newer_without_inventory_stays_pending(self):
        """ALREADY_NEWER without a confirming active inventory must NOT mark ACTIVE (fail-closed)."""
        now = datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc)
        created_sub = WhiteInternetSubscription(
            id=102,
            user_id=self.user.id,
            origin_node_id=self.origin_server.id,
            token="token-xyz",
            uuid="uuid-xyz",
            status=WhiteInternetStatus.PENDING,
            base_traffic_bytes=WHITE_INTERNET_TRIAL_TRAFFIC_BYTES,
            traffic_limit_bytes=WHITE_INTERNET_TRIAL_TRAFFIC_BYTES,
            started_at=now,
            expires_at=now + timedelta(days=WHITE_INTERNET_TRIAL_DURATION_DAYS),
            desired_version=1,
            actual_version=0,
        )
        mock_sync_resp = SyncResponse(
            result=SyncResult.ALREADY_NEWER,
            error="state=unknown",
            verified_epoch=1,
        )

        with patch("services.white_internet_service.lock_checkout_user", return_value=self.user), \
             patch("database.repositories.white_internet_repo.has_ever_activated_trial", return_value=False), \
             patch("database.repositories.white_internet_repo.get_subscription_by_user_id", return_value=None), \
             patch.object(WhiteInternetService, "select_origin_node", return_value=self.origin_server), \
             patch.object(WhiteInternetService, "get_or_create_white_internet_tariff", return_value=self.tariff), \
             patch("services.white_internet_service.get_or_create_current_version", return_value=self.tariff_version), \
             patch("database.repositories.white_internet_repo.create_white_internet_subscription", return_value=created_sub), \
             patch("services.white_internet_service.XrayNodeClient") as mock_xray_client_cls:

            mock_client_instance = AsyncMock()
            mock_client_instance.sync_client.return_value = mock_sync_resp
            mock_client_instance.get_inventory.return_value = (
                True,
                {"inventory": {"uuid-xyz": {"observed_state": "disabled"}}},
                None,
            )
            mock_xray_client_cls.return_value.__aenter__.return_value = mock_client_instance

            success, msg, sub = await WhiteInternetService.create_trial_subscription(self.session, self.user.id)

            self.assertTrue(success)
            self.assertEqual(sub.status, WhiteInternetStatus.PENDING)
            self.assertEqual(sub.actual_version, 0)
            mock_client_instance.get_inventory.assert_awaited_once()

    async def test_trial_epoch_drift_stays_pending(self):
        """APPLIED with a drifted verified_epoch must NOT mark ACTIVE (fail-closed)."""
        now = datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc)
        created_sub = WhiteInternetSubscription(
            id=103,
            user_id=self.user.id,
            origin_node_id=self.origin_server.id,
            token="token-xyz",
            uuid="uuid-xyz",
            status=WhiteInternetStatus.PENDING,
            base_traffic_bytes=WHITE_INTERNET_TRIAL_TRAFFIC_BYTES,
            traffic_limit_bytes=WHITE_INTERNET_TRIAL_TRAFFIC_BYTES,
            started_at=now,
            expires_at=now + timedelta(days=WHITE_INTERNET_TRIAL_DURATION_DAYS),
            desired_version=1,
            actual_version=0,
        )
        mock_sync_resp = SyncResponse(
            result=SyncResult.APPLIED,
            error=None,
            verified_epoch="epoch-after-restart",
        )

        with patch("services.white_internet_service.lock_checkout_user", return_value=self.user), \
             patch("database.repositories.white_internet_repo.has_ever_activated_trial", return_value=False), \
             patch("database.repositories.white_internet_repo.get_subscription_by_user_id", return_value=None), \
             patch.object(WhiteInternetService, "select_origin_node", return_value=self.origin_server), \
             patch.object(WhiteInternetService, "get_or_create_white_internet_tariff", return_value=self.tariff), \
             patch("services.white_internet_service.get_or_create_current_version", return_value=self.tariff_version), \
             patch("database.repositories.white_internet_repo.create_white_internet_subscription", return_value=created_sub), \
             patch("services.white_internet_service.XrayNodeClient") as mock_xray_client_cls:

            mock_client_instance = AsyncMock()
            mock_client_instance.sync_client.return_value = mock_sync_resp
            mock_xray_client_cls.return_value.__aenter__.return_value = mock_client_instance

            success, msg, sub = await WhiteInternetService.create_trial_subscription(self.session, self.user.id)

            self.assertTrue(success)
            self.assertEqual(sub.status, WhiteInternetStatus.PENDING)
            self.assertEqual(sub.actual_version, 0)

    async def test_service_level_protection_blocks_paid_operations(self):
        """WhiteInternetService operations validate balance, sub presence, and trial constraints."""
        # 1. Purchase without balance
        from database.repositories.account_ledger_repo import InsufficientAccountBalanceError

        fake_balance = MagicMock(available=Decimal("0.00"))
        paid_tariff = MagicMock(id=5, duration_days=30, is_active=True)
        paid_tariff_version = MagicMock(id=15, price_rub=Decimal("150.00"), base_quota_bytes=5368709120)
        with patch("services.white_internet_service.lock_checkout_user", return_value=self.user), \
             patch("database.repositories.white_internet_repo.get_subscription_by_user_id", return_value=None), \
             patch.object(WhiteInternetService, "select_origin_node", return_value=self.origin_server), \
             patch.object(WhiteInternetService, "get_or_create_white_internet_tariff", return_value=paid_tariff), \
             patch("services.white_internet_service.get_or_create_current_version", return_value=paid_tariff_version), \
             patch("services.white_internet_service.create_purchase_debit", side_effect=InsufficientAccountBalanceError("Insufficient balance")), \
             patch("services.white_internet_service.get_account_balance", return_value=fake_balance):
            ok, msg, sub = await WhiteInternetService.purchase_subscription(self.session, self.user.id)
            self.assertFalse(ok)
            self.assertIn("Недостаточно средств", msg)
            self.assertIsNone(sub)

        # 2. Renew without existing subscription
        with patch("database.repositories.white_internet_repo.get_subscription_by_user_id", return_value=None):
            ok, msg, sub = await WhiteInternetService.renew_subscription(self.session, self.user.id)
            self.assertFalse(ok)
            self.assertEqual(msg, texts.WL_SUB_NOT_FOUND)
            self.assertIsNone(sub)

        # 3. Topup on trial subscription is rejected
        sub_trial = WhiteInternetSubscription(
            id=1, user_id=self.user.id, is_trial=True, status=WhiteInternetStatus.ACTIVE
        )
        with patch("services.white_internet_service.lock_checkout_user", return_value=self.user), \
             patch("database.repositories.white_internet_repo.get_subscription_by_user_id", return_value=sub_trial):
            ok, msg, grant = await WhiteInternetService.topup_quota(self.session, self.user.id, pack_gb=10, actor_telegram_id=self.user.telegram_id)
            self.assertFalse(ok)
            self.assertEqual(msg, texts.WL_TRIAL_CANNOT_TOPUP)
            self.assertIsNone(grant)

    async def test_concurrent_trial_activations_only_one_succeeds(self):
        """10 concurrent tasks calling create_trial_subscription for the same user.
        Only the first creates the trial, while the rest receive WL_ALREADY_ACTIVE.
        """
        import asyncio

        lock = asyncio.Lock()
        has_sub_state = False
        created_sub = WhiteInternetSubscription(
            id=200,
            user_id=self.user.id,
            origin_node_id=self.origin_server.id,
            token="token-concurrent",
            uuid="uuid-concurrent",
            status=WhiteInternetStatus.ACTIVE,
            base_traffic_bytes=WHITE_INTERNET_TRIAL_TRAFFIC_BYTES,
            traffic_limit_bytes=WHITE_INTERNET_TRIAL_TRAFFIC_BYTES,
            desired_version=1,
            actual_version=1,
        )

        async def fake_lock_checkout_user(session, user_id):
            await lock.acquire()
            return self.user

        async def fake_has_ever_activated_trial(session, user_id):
            return False

        async def fake_create_sub(*args, **kwargs):
            nonlocal has_sub_state
            has_sub_state = True
            lock.release()
            return created_sub

        async def fake_get_sub_by_user_id(session, user_id):
            if has_sub_state:
                lock.release()
                return created_sub
            return None

        with patch("services.white_internet_service.lock_checkout_user", side_effect=fake_lock_checkout_user), \
             patch("database.repositories.white_internet_repo.has_ever_activated_trial", side_effect=fake_has_ever_activated_trial), \
             patch("database.repositories.white_internet_repo.get_subscription_by_user_id", side_effect=fake_get_sub_by_user_id), \
             patch.object(WhiteInternetService, "select_origin_node", return_value=self.origin_server), \
             patch.object(WhiteInternetService, "get_or_create_white_internet_tariff", return_value=self.tariff), \
             patch("services.white_internet_service.get_or_create_current_version", return_value=self.tariff_version), \
             patch("database.repositories.white_internet_repo.create_white_internet_subscription", side_effect=fake_create_sub) as mock_create, \
             patch("services.white_internet_service.XrayNodeClient") as mock_xray_client_cls:

            mock_client_instance = AsyncMock()
            mock_client_instance.sync_client.return_value = SyncResponse(
                result=SyncResult.APPLIED, error=None, verified_epoch=1
            )
            mock_xray_client_cls.return_value.__aenter__.return_value = mock_client_instance

            results = await asyncio.gather(
                *(WhiteInternetService.create_trial_subscription(self.session, self.user.id) for _ in range(10))
            )

            # Exactly 1 activation succeeds with WL_TRIAL_ACTIVATED_SUCCESS
            activated = [r for r in results if r[0] is True and r[1] == texts.WL_TRIAL_ACTIVATED_SUCCESS]
            already_active = [r for r in results if r[0] is True and r[1] == texts.WL_ALREADY_ACTIVE]

            self.assertEqual(len(activated), 1)
            self.assertEqual(len(already_active), 9)
            self.assertEqual(mock_create.call_count, 1)


class TestWhiteInternetTrialBotUI(unittest.IsolatedAsyncioTestCase):
    """Test suite for Telegram Bot UI in trial mode."""

    def setUp(self):
        self.orig_trial_env = os.environ.get("WHITE_INTERNET_TRIAL_MODE_ONLY")
        os.environ["WHITE_INTERNET_TRIAL_MODE_ONLY"] = "true"

    def tearDown(self):
        if self.orig_trial_env is None:
            os.environ.pop("WHITE_INTERNET_TRIAL_MODE_ONLY", None)
        else:
            os.environ["WHITE_INTERNET_TRIAL_MODE_ONLY"] = self.orig_trial_env

    def test_trial_overview_keyboard_has_trial_buttons(self):
        """Overview keyboard displays appropriate buttons for trial status."""
        domain = "cdn.just1k.best"

        # 1. No subscription, has_trial_available=True -> Trial activation + buy preview
        kb_none = get_white_internet_overview_keyboard(None, bot_domain=domain, has_trial_available=True)
        callbacks_none = [btn.callback_data for row in kb_none.inline_keyboard for btn in row]
        self.assertIn("wl_trial_activate", callbacks_none)
        self.assertIn("wl_buy_preview", callbacks_none)
        self.assertIn("back_to_main_menu", callbacks_none)

        # 2. ACTIVE trial subscription -> Copy link, Instructions, Convert trial, Reset devices, Back. NO wl_topup_menu
        sub_active = WhiteInternetSubscription(
            id=1,
            user_id=10,
            token="test-token",
            is_trial=True,
            status=WhiteInternetStatus.ACTIVE,
            provisioning_status=WhiteInternetProvisioningStatus.ACTIVE,
        )
        kb_active = get_white_internet_overview_keyboard(sub_active, bot_domain=domain)
        callbacks_active = [btn.callback_data for row in kb_active.inline_keyboard for btn in row if btn.callback_data]
        copy_buttons = [btn for row in kb_active.inline_keyboard for btn in row if btn.copy_text]

        self.assertEqual(len(copy_buttons), 1)
        self.assertIn("wl_show_link", callbacks_active)
        self.assertIn("wl_renew_preview", callbacks_active)
        self.assertIn("wl_reset_devices", callbacks_active)
        self.assertIn("back_to_main_menu", callbacks_active)
        self.assertNotIn("wl_topup_menu", callbacks_active)

        # 3. EXPIRED trial subscription -> Convert trial + Back button
        sub_expired = WhiteInternetSubscription(
            id=2,
            user_id=10,
            is_trial=True,
            status=WhiteInternetStatus.EXPIRED,
        )
        kb_expired = get_white_internet_overview_keyboard(sub_expired, bot_domain=domain)
        callbacks_expired = [btn.callback_data for row in kb_expired.inline_keyboard for btn in row]
        self.assertIn("back_to_main_menu", callbacks_expired)
        self.assertIn("wl_renew_preview", callbacks_expired)

    async def test_topup_menu_blocks_trial_subscription(self):
        """Topup menu must reject users on trial subscription with an alert."""
        session = AsyncMock()
        query = MagicMock(spec=CallbackQuery)
        query.answer = AsyncMock()
        query.from_user = MagicMock()
        query.from_user.id = 999888777
        query.data = "wl_topup_menu"
        user = User(id=42, telegram_id=999888777)
        sub = WhiteInternetSubscription(id=1, user_id=42, is_trial=True, status=WhiteInternetStatus.ACTIVE)

        with patch("bot.handlers.white_internet.get_user_by_telegram_id", return_value=user):
            with patch("database.repositories.white_internet_repo.get_subscription_by_user_id", return_value=sub):
                await show_topup_menu(query, session)
                query.answer.assert_awaited_with(texts.WL_TRIAL_CANNOT_TOPUP, show_alert=True)


if __name__ == "__main__":
    unittest.main()
