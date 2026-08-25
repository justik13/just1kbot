import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import quote

from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from aiogram.types import User as TelegramUser

from bot.handlers.payment.balance_routes import accept_custom_amount
from bot.handlers.start import cmd_start, parse_referral_id
from bot.keyboards.user import get_referral_keyboard
from database.models import User
from services.subscription import SubscriptionService


class TestCleanChatMessageDeletion(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.maint_patch = patch("bot.handlers.payment.balance_routes.MaintenanceService.can_user_perform_action", new=AsyncMock(return_value=True))
        self.maint_patch.start()

    async def asyncTearDown(self):
        self.maint_patch.stop()

    async def test_accept_custom_amount_deletes_invalid_text_input(self):
        message = MagicMock(spec=Message)
        message.text = "abc"
        message.bot = MagicMock()
        message.chat = MagicMock(id=123)
        message.from_user = MagicMock(id=123)
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
        message.from_user = MagicMock(id=123)
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
        message.from_user = MagicMock(id=123)
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

    async def test_chain_depth_boundaries_49_50_51(self):
        session = AsyncMock()

        # Build 52 chained users: 1 -> 2 -> 3 -> ... -> 52 -> None
        users = {
            i: User(id=i, telegram_id=i, referred_by=i + 1 if i < 52 else None)
            for i in range(1, 53)
        }

        async def fake_get_user(s, tg_id):
            return users.get(tg_id)

        with patch("services.subscription.get_user_by_telegram_id", side_effect=fake_get_user):
            # 49 hops: user 100 referred by user 4 (chain from 4 to 52 has 48 hops)
            valid_49 = await SubscriptionService._validate_referral(session, telegram_id=100, ref_id=4)
            self.assertTrue(valid_49, "49-hop chain must be valid")

            # 50 hops: user 100 referred by user 3 (chain from 3 to 52 has 49 hops)
            valid_50 = await SubscriptionService._validate_referral(session, telegram_id=100, ref_id=3)
            self.assertTrue(valid_50, "50-hop chain must be valid")

            # 51 hops: user 100 referred by user 1 (chain from 1 to 52 has 51 hops)
            valid_51 = await SubscriptionService._validate_referral(session, telegram_id=100, ref_id=1)
            self.assertFalse(valid_51, "51-hop chain must exceed MAX_REFERRAL_CHAIN_DEPTH=50 and be rejected")


class TestTopupWelcomeBonusPushNotification(unittest.IsolatedAsyncioTestCase):
    async def test_first_topup_push_includes_welcome_bonus_celebration(self):
        from decimal import Decimal

        from database.models import Payment
        from database.repositories.account_ledger_repo import AccountBalanceSnapshot
        from services.account_topup import settle_succeeded_topup
        from services.referral_bonus import ReferralBonusGrantResult
        from utils.datetime_helpers import now_utc

        session = AsyncMock()
        session.add = MagicMock()
        payment = Payment(
            id=100,
            user_id=20,
            amount=Decimal(500),
            currency="RUB",
            provider_status="succeeded",
            provider_confirmed_at=now_utc(),
            fulfillment_status="pending",
            credited_at=None,
            topup_context={},
        )
        user = User(
            id=20,
            telegram_id=2000,
            referred_by=1000,
            is_deleted=False,
            is_bot_blocked=False,
        )

        bot = MagicMock()

        mock_settings = MagicMock(BALANCE_MAX_AVAILABLE_RUB="100000")

        with patch("services.account_topup.lock_checkout_user", AsyncMock(return_value=user)), \
        patch("services.account_topup.get_account_balance", AsyncMock(return_value=AccountBalanceSnapshot(
            accounting_position=Decimal(550),
            available=Decimal(550),
            reserved=Decimal(0),
            debt=Decimal(0),
            real_position=Decimal(500),
            bonus_position=Decimal(50),
            real_available=Decimal(500),
            bonus_available=Decimal(50),
        ))), \
        patch("services.account_topup.credit_succeeded_topup", AsyncMock(return_value=(MagicMock(), True))), \
        patch("services.account_topup.refresh_user_dispute_hold", AsyncMock()), \
        patch("services.referral_bonus.grant_referral_bonus_for_topup", AsyncMock(return_value=ReferralBonusGrantResult(
            referrer_bonus=Decimal(50),
            purchaser_welcome_bonus=Decimal(50),
        ))):
            await settle_succeeded_topup(session, payment=payment, source="test", settings=mock_settings, bot=bot)

        self.assertEqual(payment.topup_context.get("purchaser_welcome_bonus"), 50)
        self.assertEqual(payment.topup_context.get("referrer_bonus"), 50)

    async def test_subsequent_topup_push_excludes_welcome_bonus(self):
        from decimal import Decimal

        from database.models import Payment
        from database.repositories.account_ledger_repo import AccountBalanceSnapshot
        from services.account_topup import settle_succeeded_topup
        from services.referral_bonus import ReferralBonusGrantResult
        from utils.datetime_helpers import now_utc

        session = AsyncMock()
        session.add = MagicMock()
        payment = Payment(
            id=101,
            user_id=20,
            amount=Decimal(1000),
            currency="RUB",
            provider_status="succeeded",
            provider_confirmed_at=now_utc(),
            fulfillment_status="pending",
            credited_at=None,
            topup_context={},
        )
        user = User(
            id=20,
            telegram_id=2000,
            referred_by=1000,
            is_deleted=False,
            is_bot_blocked=False,
        )

        bot = MagicMock()
        mock_settings = MagicMock(BALANCE_MAX_AVAILABLE_RUB="100000")

        with patch("services.account_topup.lock_checkout_user", AsyncMock(return_value=user)), \
        patch("services.account_topup.get_account_balance", AsyncMock(return_value=AccountBalanceSnapshot(
            accounting_position=Decimal(1550),
            available=Decimal(1550),
            reserved=Decimal(0),
            debt=Decimal(0),
            real_position=Decimal(1500),
            bonus_position=Decimal(50),
            real_available=Decimal(1500),
            bonus_available=Decimal(50),
        ))), \
        patch("services.account_topup.credit_succeeded_topup", AsyncMock(return_value=(MagicMock(), True))), \
        patch("services.account_topup.refresh_user_dispute_hold", AsyncMock()), \
        patch("services.referral_bonus.grant_referral_bonus_for_topup", AsyncMock(return_value=ReferralBonusGrantResult(
            referrer_bonus=Decimal(100),
            purchaser_welcome_bonus=Decimal(0),
        ))):
            await settle_succeeded_topup(session, payment=payment, source="test", settings=mock_settings, bot=bot)

        self.assertEqual(payment.topup_context.get("purchaser_welcome_bonus"), 0)
        self.assertEqual(payment.topup_context.get("referrer_bonus"), 100)


class TestReferralPaginationClamping(unittest.IsolatedAsyncioTestCase):
    async def test_get_user_referrals_paginated_clamps_out_of_bounds_page(self):
        from database.repositories.users_repo import get_user_referrals_paginated

        session = AsyncMock()
        # Mock count = 21 (3 pages of 10 items)
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [User(id=21, telegram_id=2021, username="ref21")]
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars

        with patch("database.repositories.users_repo.get_user_referrals_count", AsyncMock(return_value=21)):
            session.execute = AsyncMock(return_value=mock_result)

            # Request page 999
            items, total_count, normalized_page = await get_user_referrals_paginated(
                session, telegram_id=1000, page=999, per_page=10
            )

        self.assertEqual(total_count, 21)
        self.assertEqual(normalized_page, 3, "Page 999 must be clamped to last page 3")
        self.assertEqual(len(items), 1)

        # Verify offset in query was (3 - 1) * 10 = 20
        executed_stmt = session.execute.call_args[0][0]
        self.assertEqual(executed_stmt._offset, 20)
        self.assertEqual(executed_stmt._limit, 10)

    async def test_show_referrals_list_renders_last_page_on_out_of_bounds_page(self):
        from aiogram.types import CallbackQuery

        from bot.handlers.profile import show_referrals_list

        callback = MagicMock(spec=CallbackQuery)
        callback.data = "referrals_list:999"
        callback.message = MagicMock()
        callback.message.chat = MagicMock(id=1000)
        callback.message.message_id = 555
        callback.answer = AsyncMock()
        callback.bot = MagicMock()

        state = MagicMock(spec=FSMContext)
        state.clear = AsyncMock()
        session = AsyncMock()
        db_user = User(id=1, telegram_id=1000, username="owner")

        last_page_referral = User(id=21, telegram_id=2021, username="ref21")
        rendered_texts = []

        async def fake_render(bot, chat_id, text, reply_markup, trigger_message_id=None):
            rendered_texts.append((text, reply_markup))

        with patch("bot.handlers.profile.get_user_referrals_paginated", AsyncMock(return_value=([last_page_referral], 21, 3))), \
             patch("bot.handlers.profile.render_hub", side_effect=fake_render):
            await show_referrals_list(callback, state, session, db_user=db_user)

        self.assertEqual(len(rendered_texts), 1)
        text, markup = rendered_texts[0]
        self.assertIn("@ref21", text)
        self.assertIn("21. ", text)
        self.assertIn("Всего приглашено: 21", text)

        # Verify pagination button shows 3/3
        buttons = [btn for row in markup.inline_keyboard for btn in row]
        page_btn = next((b for b in buttons if "3/3" in b.text), None)
        self.assertIsNotNone(page_btn, "Pagination indicator must show 3/3")


class TestShareButtonAndKeyboards(unittest.TestCase):
    def test_referral_keyboard_includes_encoded_share_button(self):
        from bot import texts

        link = "https://t.me/mybot?start=ref_12345"
        kb = get_referral_keyboard(referral_link=link)
        self.assertIsNotNone(kb)

        buttons = [btn for row in kb.inline_keyboard for btn in row]
        share_btn = next((b for b in buttons if b.text == "↗️ Поделиться"), None)
        self.assertIsNotNone(share_btn, "Share button must be present in referral keyboard")
        self.assertIn("https://t.me/share/url?url=", share_btn.url)
        self.assertIn(quote(link, safe=""), share_btn.url)
        self.assertIn("&text=", share_btn.url)
        self.assertIn(quote(texts.REFERRAL_SHARE_TEXT, safe=""), share_btn.url)


if __name__ == "__main__":
    unittest.main()
