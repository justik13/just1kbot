"""Unit tests for legacy and archived tariff renewal fallback logic."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
import unittest
from unittest.mock import AsyncMock, patch

from bot import texts
from bot.handlers.payment.showcase_routes import render_quick_renew
from database.models import Tariff, User
from services.subscription import SubscriptionService
from utils.datetime_helpers import now_utc


class TestLegacyTariffRenewalAndFallback(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.bot = AsyncMock()
        self.session = AsyncMock()
        self.chat_id = 123456789

        # Active AWG tariffs standard pool
        self.t_basic_7 = Tariff(
            id=2, name="Базовый", service_type="awg", price_rub=Decimal(35), duration_days=7, device_limit=2, is_active=True
        )
        self.t_basic_30 = Tariff(
            id=3, name="Базовый", service_type="awg", price_rub=Decimal(90), duration_days=30, device_limit=2, is_active=True
        )
        self.t_basic_90 = Tariff(
            id=4, name="Базовый", service_type="awg", price_rub=Decimal(240), duration_days=90, device_limit=2, is_active=True
        )
        self.t_family_30 = Tariff(
            id=5, name="Семейный", service_type="awg", price_rub=Decimal(180), duration_days=30, device_limit=5, is_active=True
        )
        self.t_pro_30 = Tariff(
            id=7, name="Pro", service_type="awg", price_rub=Decimal(320), duration_days=30, device_limit=10, is_active=True
        )
        self.all_tariffs = [
            self.t_basic_7,
            self.t_basic_30,
            self.t_basic_90,
            self.t_family_30,
            self.t_pro_30,
        ]

    @patch("bot.handlers.payment.showcase_routes.render_hub")
    @patch("bot.handlers.payment.showcase_routes.MaintenanceService.can_user_perform_action", return_value=True)
    @patch("bot.handlers.payment.showcase_routes.get_active_tariffs")
    @patch("bot.handlers.payment.showcase_routes._get_effective_device_limit")
    async def test_legacy_user_1_device_falls_back_to_basic_tier(
        self, mock_get_limit, mock_get_tariffs, mock_maint, mock_render_hub
    ):
        """Legacy user with 1 device limit should see active 'Базовый' 2-device tariffs for renewal."""
        db_user = User(
            id=1,
            telegram_id=123456789,
            device_limit=1,
            current_tariff_id=None,
            subscription_end=now_utc() + timedelta(days=5),
        )
        mock_get_limit.return_value = 1
        mock_get_tariffs.return_value = self.all_tariffs

        await render_quick_renew(self.bot, self.chat_id, self.session, db_user)

        mock_render_hub.assert_called_once()
        call_args = mock_render_hub.call_args
        rendered_text = call_args[0][2]
        keyboard = call_args[0][3]

        # Must display Базовый as current tariff name
        self.assertIn("Базовый", rendered_text)
        # Keyboard must contain renewal buttons for 7, 30, 90 days (device_limit=2)
        button_texts = [btn.text for row in keyboard.inline_keyboard for btn in row]
        self.assertTrue(any("7" in t and "35" in t for t in button_texts))
        self.assertTrue(any("30" in t and "90" in t for t in button_texts))
        self.assertTrue(any("90" in t and "240" in t for t in button_texts))

    @patch("bot.handlers.payment.showcase_routes.render_hub")
    @patch("bot.handlers.payment.showcase_routes.MaintenanceService.can_user_perform_action", return_value=True)
    @patch("bot.handlers.payment.showcase_routes.get_active_tariffs")
    @patch("bot.handlers.payment.showcase_routes._get_effective_device_limit")
    async def test_discontinued_tier_shows_archived_notice_and_tariff_choice(
        self, mock_get_limit, mock_get_tariffs, mock_maint, mock_render_hub
    ):
        """If user has discontinued tier and no matching tier exists, show archived notice and change keyboard."""
        db_user = User(
            id=2,
            telegram_id=987654321,
            device_limit=1,
            current_tariff_id=None,
            subscription_end=now_utc() + timedelta(days=5),
        )
        mock_get_limit.return_value = 1
        # Suppose only Pro tariffs are active, no Basic tariffs
        mock_get_tariffs.return_value = [self.t_pro_30]

        await render_quick_renew(self.bot, self.chat_id, self.session, db_user)

        mock_render_hub.assert_called_once()
        call_args = mock_render_hub.call_args
        rendered_text = call_args[0][2]
        keyboard = call_args[0][3]

        # Must display archived notice, NOT "нет доступных тарифов"
        self.assertEqual(rendered_text, texts.PAYMENT_ARCHIVED_TARIFF_NOTICE)
        self.assertNotIn("В данный момент нет доступных тарифов", rendered_text)
        # Keyboard must allow choosing available active tariffs
        button_texts = [btn.text for row in keyboard.inline_keyboard for btn in row]
        self.assertTrue(any("Pro" in t for t in button_texts))

    @patch("bot.handlers.payment.showcase_routes.render_hub")
    @patch("bot.handlers.payment.showcase_routes.MaintenanceService.can_user_perform_action", return_value=True)
    @patch("bot.handlers.payment.showcase_routes.get_active_tariffs")
    async def test_no_active_tariffs_at_all_shows_no_tariffs_message(
        self, mock_get_tariffs, mock_maint, mock_render_hub
    ):
        """When system has zero active tariffs, show PAYMENT_NO_TARIFFS."""
        db_user = User(
            id=3,
            telegram_id=111222333,
            device_limit=2,
            subscription_end=now_utc() + timedelta(days=5),
        )
        mock_get_tariffs.return_value = []

        await render_quick_renew(self.bot, self.chat_id, self.session, db_user)

        mock_render_hub.assert_called_once()
        rendered_text = mock_render_hub.call_args[0][2]
        self.assertEqual(rendered_text, texts.PAYMENT_NO_TARIFFS)


class TestSubscriptionServiceDeviceLimitIsolation(unittest.IsolatedAsyncioTestCase):
    @patch("services.subscription.get_tariff_by_id")
    async def test_white_internet_tariff_does_not_override_awg_device_limit(self, mock_get_tariff):
        """White Internet tariff must not pollute standard AWG device limit."""
        session = AsyncMock()
        # Tariff 1 is White Internet with 1 device
        wl_tariff = Tariff(id=1, name="Белый Интернет 50 ГБ", service_type="white_internet", device_limit=1)
        mock_get_tariff.return_value = wl_tariff

        user = User(id=10, current_tariff_id=1, device_limit=5)
        limit = await SubscriptionService.get_effective_device_limit(session, user)

        # Must ignore white_internet tariff and fall back to user's AWG device_limit
        self.assertEqual(limit, 5)

    @patch("services.subscription.get_tariff_by_id")
    async def test_awg_tariff_sets_device_limit(self, mock_get_tariff):
        """AWG tariff correctly determines standard device limit."""
        session = AsyncMock()
        awg_tariff = Tariff(id=3, name="Базовый", service_type="awg", device_limit=2)
        mock_get_tariff.return_value = awg_tariff

        user = User(id=10, current_tariff_id=3, device_limit=5)
        limit = await SubscriptionService.get_effective_device_limit(session, user)

        self.assertEqual(limit, 2)
