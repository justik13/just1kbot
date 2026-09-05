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

import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.handlers.white_internet import (
    get_white_internet_overview_keyboard,
    process_white_internet_buy,
    process_white_internet_renew,
    process_topup_pack,
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
             patch("database.repositories.white_internet_repo.has_user_any_subscription", return_value=False), \
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
             patch("database.repositories.white_internet_repo.has_user_any_subscription", return_value=True), \
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
             patch("database.repositories.white_internet_repo.has_user_any_subscription", return_value=False), \
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

    async def test_service_level_protection_blocks_paid_operations(self):
        """WhiteInternetService must reject purchase, renew, and topup in trial mode."""
        # 1. Purchase
        ok, msg, sub = await WhiteInternetService.purchase_subscription(self.session, self.user.id)
        self.assertFalse(ok)
        self.assertEqual(msg, texts.WL_PAID_FEATURES_DISABLED_ALERT)
        self.assertIsNone(sub)

        # 2. Renew
        ok, msg, sub = await WhiteInternetService.renew_subscription(self.session, self.user.id)
        self.assertFalse(ok)
        self.assertEqual(msg, texts.WL_PAID_FEATURES_DISABLED_ALERT)
        self.assertIsNone(sub)

        # 3. Topup
        ok, msg, grant = await WhiteInternetService.topup_quota(self.session, self.user.id, pack_gb=10)
        self.assertFalse(ok)
        self.assertEqual(msg, texts.WL_PAID_FEATURES_DISABLED_ALERT)
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

        async def fake_has_user_any_subscription(session, user_id):
            return has_sub_state

        async def fake_create_sub(*args, **kwargs):
            nonlocal has_sub_state
            has_sub_state = True
            lock.release()
            return created_sub

        async def fake_get_sub_by_user_id(session, user_id):
            lock.release()
            return created_sub

        with patch("services.white_internet_service.lock_checkout_user", side_effect=fake_lock_checkout_user), \
             patch("database.repositories.white_internet_repo.has_user_any_subscription", side_effect=fake_has_user_any_subscription), \
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

    def test_trial_overview_keyboard_has_no_paid_buttons(self):
        """In trial mode, overview keyboard must offer trial activation, copy link, and refresh without paid buttons."""
        domain = "cdn.just1k.best"

        # 1. No subscription -> Trial activation button only, NO wl_buy_confirm
        kb_none = get_white_internet_overview_keyboard(None, bot_domain=domain)
        callbacks_none = [btn.callback_data for row in kb_none.inline_keyboard for btn in row]
        self.assertIn("wl_trial_activate", callbacks_none)
        self.assertNotIn("wl_buy_confirm", callbacks_none)
        self.assertIn("back_to_main_menu", callbacks_none)

        # 2. ACTIVE subscription -> Copy link, Instructions, Refresh, Back. NO wl_topup_menu, NO wl_renew_confirm
        sub_active = WhiteInternetSubscription(
            id=1,
            user_id=10,
            token="test-token",
            status=WhiteInternetStatus.ACTIVE,
            provisioning_status=WhiteInternetProvisioningStatus.ACTIVE,
        )
        kb_active = get_white_internet_overview_keyboard(sub_active, bot_domain=domain)
        callbacks_active = [btn.callback_data for row in kb_active.inline_keyboard for btn in row if btn.callback_data]
        copy_buttons = [btn for row in kb_active.inline_keyboard for btn in row if btn.copy_text]

        self.assertEqual(len(copy_buttons), 1)
        self.assertIn("wl_show_link", callbacks_active)
        self.assertIn("white_internet", callbacks_active)
        self.assertIn("back_to_main_menu", callbacks_active)
        self.assertNotIn("wl_topup_menu", callbacks_active)
        self.assertNotIn("wl_renew_confirm", callbacks_active)

        # 3. EXPIRED subscription -> Back button only. NO wl_renew_confirm
        sub_expired = WhiteInternetSubscription(
            id=2,
            user_id=10,
            status=WhiteInternetStatus.EXPIRED,
        )
        kb_expired = get_white_internet_overview_keyboard(sub_expired, bot_domain=domain)
        callbacks_expired = [btn.callback_data for row in kb_expired.inline_keyboard for btn in row]
        self.assertIn("back_to_main_menu", callbacks_expired)
        self.assertNotIn("wl_renew_confirm", callbacks_expired)

    async def test_stale_paid_callbacks_blocked_with_alert(self):
        """Clicking stale paid buttons must answer with an informative alert and never charge money."""
        session = AsyncMock()
        query = MagicMock(spec=CallbackQuery)
        query.answer = AsyncMock()
        query.data = "wl_buy_confirm"

        # 1. Buy callback
        await process_white_internet_buy(query, session)
        query.answer.assert_awaited_with(texts.WL_PAID_FEATURES_DISABLED_ALERT, show_alert=True)

        # 2. Renew callback
        query.answer.reset_mock()
        query.data = "wl_renew_confirm"
        await process_white_internet_renew(query, session)
        query.answer.assert_awaited_with(texts.WL_PAID_FEATURES_DISABLED_ALERT, show_alert=True)

        # 3. Topup menu callback
        query.answer.reset_mock()
        query.data = "wl_topup_menu"
        await show_topup_menu(query, session)
        query.answer.assert_awaited_with(texts.WL_PAID_FEATURES_DISABLED_ALERT, show_alert=True)

        # 4. Topup pack callback
        query.answer.reset_mock()
        query.data = "wl_topup_pack_10"
        await process_topup_pack(query, session)
        query.answer.assert_awaited_with(texts.WL_PAID_FEATURES_DISABLED_ALERT, show_alert=True)


if __name__ == "__main__":
    unittest.main()
