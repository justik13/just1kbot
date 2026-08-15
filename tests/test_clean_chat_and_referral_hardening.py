"""Tests for clean chat message deletion, late binding hardening, cycle detection, and SQL pagination."""

import asyncio
from decimal import Decimal
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import quote

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Chat, Message, User as TelegramUser

from bot.handlers.payment.balance_routes import accept_custom_amount
from bot.handlers.start import cmd_start, parse_referral_id
from bot.keyboards.user import get_referral_keyboard
from bot.states import BalanceStates
from database.models import User
from database.repositories.payments_repo import has_successful_topup
from database.repositories.users_repo import (
    get_user_referrals_count,
    get_user_referrals_paginated,
)
from services.subscription import MAX_REFERRAL_CHAIN_DEPTH, SubscriptionService


class TestCleanChatMessageDeletion(unittest.IsolatedAsyncioTestCase):
    async def test_accept_custom_amount_deletes_invalid_text_input(self):
        message = MagicMock(spec=Message)
        message.text = "abc"
        message.bot = MagicMock()
        message.chat = MagicMock(id=123)
        message.message_id = 999
        message.delete = AsyncMock()

        state = MagicMock(spec=FSMContext)
        session = AsyncMock()
        db_user = MagicMock(spec=User)

        with patch("bot.handlers.payment.balance_routes.render_hub", new_callable=AsyncMock) as mock_render:
            await accept_custom_amount(message, state, session, db_user=db_user)

        message.delete.assert_awaited_once()
        mock_render.assert_awaited_once()

    async def test_accept_custom_amount_deletes_amount_below_minimum(self):
        message = MagicMock(spec=Message)
        message.text = "5"
        message.bot = MagicMock()
        message.chat = MagicMock(id=123)
        message.message_id = 999
        message.delete = AsyncMock()

        state = MagicMock(spec=FSMContext)
        state.get_data = AsyncMock(return_value={"balance_minimum": 100})
        session = AsyncMock()
        db_user = MagicMock(spec=User)

        with patch("bot.handlers.payment.balance_routes.render_hub", new_callable=AsyncMock) as mock_render:
            await accept_custom_amount(message, state, session, db_user=db_user)

        message.delete.assert_awaited_once()
        mock_render.assert_awaited_once()

    async def test_accept_custom_amount_deletes_valid_amount_input(self):
        message = MagicMock(spec=Message)
        message.text = "500"
        message.bot = MagicMock()
        message.chat = MagicMock(id=123)
        message.message_id = 999
        message.delete = AsyncMock()

        state = MagicMock(spec=FSMContext)
        state.get_data = AsyncMock(return_value={"balance_minimum": 100})
        state.clear = AsyncMock()
        session = AsyncMock()
        db_user = MagicMock(spec=User)

        with patch("bot.handlers.payment.balance_routes._create_and_render_topup", new_callable=AsyncMock) as mock_create:
            await accept_custom_amount(message, state, session, db_user=db_user)

        message.delete.assert_awaited_once()
        state.clear.assert_awaited_once()
        mock_create.assert_awaited_once()

    async def test_cmd_start_deletes_incoming_message(self):
        message = MagicMock(spec=Message)
        message.from_user = TelegramUser(id=12345, is_bot=False, first_name="Test", username="testuser")
        message.chat = MagicMock(id=12345)
        message.message_id = 777
        message.bot = MagicMock()
        message.delete = AsyncMock()
        message.answer = AsyncMock(return_value=MagicMock(message_id=888))

        state = MagicMock(spec=FSMContext)
        state.clear = AsyncMock()
        command = MagicMock(args=None)
        session = AsyncMock()

        dummy_user = User(id=1, telegram_id=12345, username="testuser", first_name="Test")

        with patch("services.subscription.SubscriptionService.process_onboarding", AsyncMock(return_value=dummy_user)), \
             patch("bot.handlers.start._update_user_profile_if_changed", AsyncMock(return_value=dummy_user)), \
             patch("bot.handlers.start._ensure_bot_unblocked", AsyncMock()), \
             patch("bot.handlers.start.render_hub", AsyncMock()):
            await cmd_start(message, state, command, session)

        message.delete.assert_awaited_once()
        state.clear.assert_awaited_once()


class TestReferralIdParsing(unittest.TestCase):
    def test_valid_referral_ids(self):
        self.assertEqual(parse_referral_id("ref_123456"), 123456)
        self.assertEqual(parse_referral_id("123456"), 123456)
        self.assertEqual(parse_referral_id("ref_9223372036854775807"), 9223372036854775807)

    def test_invalid_and_overflow_referral_ids(self):
        self.assertIsNone(parse_referral_id(None))
        self.assertIsNone(parse_referral_id(""))
        self.assertIsNone(parse_referral_id("abc"))
        self.assertIsNone(parse_referral_id("ref_abc"))
        self.assertIsNone(parse_referral_id("99999999999999999999999999999999999999999"))
        self.assertIsNone(parse_referral_id("-123"))
        self.assertIsNone(parse_referral_id("0"))


class TestLateBindingPolicy(unittest.IsolatedAsyncioTestCase):
    async def test_late_binding_allowed_if_no_successful_topups(self):
        session = AsyncMock()
        existing_user = User(id=10, telegram_id=1000, referred_by=None, is_deleted=False, is_bot_blocked=False)

        with patch("services.subscription.get_user_by_telegram_id_any", AsyncMock(return_value=existing_user)), \
             patch("database.repositories.payments_repo.has_successful_topup", AsyncMock(return_value=False)), \
             patch("services.subscription.SubscriptionService._validate_referral", AsyncMock(return_value=True)), \
             patch("services.subscription.invalidate_user_cache"):
            user = await SubscriptionService.process_onboarding(
                session,
                telegram_id=1000,
                username="test",
                first_name="Test",
                ref_id=2000,
            )

        self.assertEqual(user.referred_by, 2000)

    async def test_late_binding_forbidden_if_user_already_has_successful_topup(self):
        session = AsyncMock()
        existing_user = User(id=10, telegram_id=1000, referred_by=None, is_deleted=False, is_bot_blocked=False)

        with patch("services.subscription.get_user_by_telegram_id_any", AsyncMock(return_value=existing_user)), \
             patch("database.repositories.payments_repo.has_successful_topup", AsyncMock(return_value=True)), \
             patch("services.subscription.SubscriptionService._validate_referral", AsyncMock(return_value=True)), \
             patch("services.subscription.invalidate_user_cache"):
            user = await SubscriptionService.process_onboarding(
                session,
                telegram_id=1000,
                username="test",
                first_name="Test",
                ref_id=2000,
            )

        self.assertIsNone(user.referred_by, "Late binding must be rejected if user has payment history")


class TestReferralCycleDetection(unittest.IsolatedAsyncioTestCase):
    async def test_deep_cycle_detected_and_rejected(self):
        session = AsyncMock()

        # Build a chain: 1 -> 2 -> 3 -> 4 -> 5 -> 6 -> 7 -> 8
        # Now 8 tries to be referred by 1 (which would create a cycle: 1 -> ... -> 8 -> 1)
        users = {
            1: User(id=1, telegram_id=1, referred_by=2),
            2: User(id=2, telegram_id=2, referred_by=3),
            3: User(id=3, telegram_id=3, referred_by=4),
            4: User(id=4, telegram_id=4, referred_by=5),
            5: User(id=5, telegram_id=5, referred_by=6),
            6: User(id=6, telegram_id=6, referred_by=7),
            7: User(id=7, telegram_id=7, referred_by=8),
            8: User(id=8, telegram_id=8, referred_by=None),
        }

        async def fake_get_user(s, tg_id):
            return users.get(tg_id)

        with patch("services.subscription.get_user_by_telegram_id", side_effect=fake_get_user):
            # User 8 attempts to bind to referrer 1
            is_valid = await SubscriptionService._validate_referral(session, telegram_id=8, ref_id=1)

        self.assertFalse(is_valid, "Deep circular referral chain (>5 levels) must be detected and rejected")

    async def test_max_chain_depth_limit(self):
        session = AsyncMock()

        # Create 60 chained users without cycle
        users = {
            i: User(id=i, telegram_id=i, referred_by=i + 1)
            for i in range(1, 65)
        }

        async def fake_get_user(s, tg_id):
            return users.get(tg_id)

        with patch("services.subscription.get_user_by_telegram_id", side_effect=fake_get_user):
            is_valid = await SubscriptionService._validate_referral(session, telegram_id=100, ref_id=1)

        self.assertFalse(is_valid, "Chains exceeding MAX_REFERRAL_CHAIN_DEPTH must be rejected")


class TestShareButtonAndKeyboards(unittest.TestCase):
    def test_referral_keyboard_includes_encoded_share_button(self):
        link = "https://t.me/mybot?start=ref_12345"
        kb = get_referral_keyboard(referral_link=link)
        self.assertIsNotNone(kb)

        buttons = [btn for row in kb.inline_keyboard for btn in row]
        share_btn = next((b for b in buttons if b.text == "↗️ Поделиться"), None)
        self.assertIsNotNone(share_btn, "Share button must be present in referral keyboard")
        self.assertIn("https://t.me/share/url?url=", share_btn.url)
        self.assertIn(quote(link, safe=""), share_btn.url)


if __name__ == "__main__":
    unittest.main()
