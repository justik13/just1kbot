import unittest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

from bot.handlers.admin.users.common import _build_users_list_text_and_kb
from bot.keyboards.device import get_device_keyboard
from database.models import (
    AuditLog,
    Tariff,
    User,
)
from database.repositories.purchases_repo import (
    get_purchase_log_by_id,
    get_purchase_logs_paginated,
)
from utils.datetime_helpers import now_utc


class AdminPurchasesAndFiltersTests(unittest.IsolatedAsyncioTestCase):

    def test_admin_dashboard_keyboard_has_purchases(self):
        from bot.keyboards.admin.dashboard import get_admin_cat_finance_keyboard
        markup = get_admin_cat_finance_keyboard()
        all_callbacks = [
            btn.callback_data
            for row in markup.inline_keyboard
            for btn in row
        ]
        self.assertIn("admin_purchases", all_callbacks)

    def test_device_keyboard_has_support_help(self):
        markup = get_device_keyboard(profile_id=42)
        all_callbacks = [
            btn.callback_data
            for row in markup.inline_keyboard
            for btn in row
        ]
        self.assertTrue(any(cb.startswith("support_help") for cb in all_callbacks))

    async def test_users_list_keyboard_banned_filter(self):
        u1 = User(telegram_id=2001, username="test_grid_user")
        users = [u1]
        rendered, builder = await _build_users_list_text_and_kb(
            users, page=1, total_pages=1, total=1, filter_type="all"
        )
        markup = builder.as_markup()
        all_callbacks = [
            btn.callback_data
            for row in markup.inline_keyboard
            for btn in row
        ]
        self.assertIn("admin_users_filter:new_7d:none:1", all_callbacks)
        self.assertIn("admin_users_filter:expiring_3d:none:1", all_callbacks)
        self.assertIn("admin_users_filter:active:none:1", all_callbacks)
        self.assertIn("admin_users_filter:expired:none:1", all_callbacks)
        self.assertIn("admin_users_filter:banned:none:1", all_callbacks)
        self.assertIn("admin_users_filter_menu:server", all_callbacks)
        self.assertIn("admin_users_filter_menu:tariff", all_callbacks)

    def test_apply_user_filters_logic(self):
        from sqlalchemy import select

        from database.repositories.users_repo import _apply_user_filters

        stmt_all = _apply_user_filters(select(User), "all")
        stmt_new = _apply_user_filters(select(User), "new_7d")
        stmt_new_24h = _apply_user_filters(select(User), "new_24h")
        stmt_expiring = _apply_user_filters(select(User), "expiring_3d")
        stmt_active = _apply_user_filters(select(User), "active")
        stmt_expired = _apply_user_filters(select(User), "expired")
        stmt_banned = _apply_user_filters(select(User), "banned")
        stmt_server = _apply_user_filters(select(User), "server", filter_param="1")
        stmt_tariff = _apply_user_filters(select(User), "tariff", filter_param="2")

        self.assertIn("select users.id", str(stmt_all).lower())
        self.assertIn("created_at >=", str(stmt_new))
        self.assertIn("created_at >=", str(stmt_new_24h))
        self.assertIn("subscription_end >", str(stmt_expiring))
        self.assertIn("subscription_end <=", str(stmt_expiring))
        self.assertIn("subscription_end >", str(stmt_active))
        self.assertIn("subscription_end is not null", str(stmt_expired).lower())
        self.assertIn("is_banned is true", str(stmt_banned).lower())
        self.assertIn("is_bot_blocked is true", str(stmt_banned).lower())
        self.assertIn("server_id =", str(stmt_server).lower())
        self.assertIn("current_tariff_id in", str(stmt_tariff).lower())
    async def test_purchases_repo_mocked(self):
        session = AsyncMock()

        user = User(id=10, telegram_id=3001, username="buyer_user")
        tariff = Tariff(
            id=1,
            name="Test Tariff 30d",
            duration_days=30,
            device_limit=2,
            price_rub=Decimal("199.00"),
            is_active=True,
        )
        import uuid
        now = now_utc()
        order = MagicMock()
        order.id = uuid.uuid4()
        order.user_id = 10
        order.user = user
        order.service_type = "white_internet"
        order.tariff = tariff
        order.metadata_ = {"operation": "purchase"}
        order.amount_rub = Decimal("199.00")
        order.status = "paid"
        order.device_limit = 2
        order.duration_days = 30
        order.paid_at = now
        order.created_at = now

        audit_log = AuditLog(
            id=200,
            admin_id=123456789,
            action="ADMIN_SUB_GRANT",
            target_type="User",
            target_id=10,
            details="days=30",
            created_at=now,
        )

        res_orders = MagicMock()
        res_orders.scalars().all.return_value = [order]

        res_audit = MagicMock()
        res_audit.scalars().all.return_value = [audit_log]

        res_users = MagicMock()
        res_users.all.return_value = [user]

        session.execute.side_effect = [res_orders, res_audit]
        session.scalars.return_value = res_users

        entries, total = await get_purchase_logs_paginated(session, page=1, per_page=10)
        self.assertEqual(total, 2)
        self.assertEqual(entries[0].id, f"order_{order.id}")
        self.assertEqual(entries[0].amount_rub, Decimal("199.00"))
        self.assertEqual(entries[0].tariff_name, "Test Tariff 30d")
        # List view carries no funds split (see purchase card).
        self.assertIsNone(entries[0].real_amount_rub)
        self.assertIsNone(entries[0].bonus_amount_rub)
        self.assertEqual(entries[1].id, "audit_200")

    async def test_purchase_card_quote_shows_funds_split(self):
        import uuid

        session = AsyncMock()
        now = now_utc()
        user = User(id=10, telegram_id=3001, username="buyer_user")
        tariff = Tariff(
            id=1,
            name="Test Tariff 30d",
            duration_days=30,
            device_limit=2,
            price_rub=Decimal("199.00"),
            is_active=True,
        )
        order_id = uuid.uuid4()
        order = MagicMock()
        order.id = order_id
        order.user_id = 10
        order.user = user
        order.service_type = "white_internet"
        order.tariff = tariff
        order.metadata_ = {"operation": "purchase"}
        order.amount_rub = Decimal("199.00")
        order.status = "paid"
        order.device_limit = 2
        order.duration_days = 30
        order.paid_at = now
        order.created_at = now

        res_order = MagicMock()
        res_order.scalar_one_or_none.return_value = order

        debit = MagicMock()
        debit.id = 7
        debit.quote_id = None
        debit.order_id = order_id
        res_debits = MagicMock()
        res_debits.scalars().all.return_value = [debit]

        res_alloc = MagicMock()
        res_alloc.all.return_value = [
            (7, "payment_credit", Decimal("100.00")),
            (7, "admin_adjustment", Decimal("99.00")),
        ]

        session.execute.side_effect = [res_order, res_debits, res_alloc]

        entry = await get_purchase_log_by_id(session, f"order_{order_id}")
        self.assertIsNotNone(entry)
        self.assertEqual(entry.real_amount_rub, Decimal("100.00"))
        self.assertEqual(entry.bonus_amount_rub, Decimal("99.00"))

    async def test_purchase_card_wallet_order_without_allocations_shows_no_split(self):
        import uuid

        session = AsyncMock()
        user = User(id=11, telegram_id=3002, username="wallet_buyer")

        order = MagicMock()
        order.id = uuid.uuid4()
        order.service_type = "awg"
        order.payment_method = "wallet"
        order.tariff = None
        order.metadata_ = {}
        order.device_limit = 1
        order.duration_days = 30
        order.amount_rub = Decimal("199.00")
        order.paid_at = now_utc()
        order.created_at = now_utc()
        order.user = user

        res_order = MagicMock()
        res_order.scalar_one_or_none.return_value = order

        res_debits = MagicMock()
        res_debits.scalars().all.return_value = []

        session.execute.side_effect = [res_order, res_debits]

        entry = await get_purchase_log_by_id(session, f"order_{order.id}")
        self.assertIsNotNone(entry)
        self.assertEqual(entry.amount_rub, Decimal("199.00"))
        self.assertIsNone(entry.real_amount_rub)
        self.assertIsNone(entry.bonus_amount_rub)


if __name__ == "__main__":
    unittest.main()
