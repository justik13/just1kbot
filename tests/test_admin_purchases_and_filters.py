import unittest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

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
        self.assertEqual(entry.payment_method, "wallet")

    async def test_purchases_repo_excludes_topup_and_resolves_slot_and_quota(self):
        import uuid

        session = AsyncMock()
        user = User(id=15, telegram_id=3005, username="wi_buyer")

        slot_order = MagicMock()
        slot_order.id = uuid.uuid4()
        slot_order.service_type = "white_internet"
        slot_order.payment_method = "wallet"
        slot_order.tariff = None
        slot_order.metadata_ = {"operation": "add_device_slot"}
        slot_order.device_limit = 3
        slot_order.traffic_bytes = 0
        slot_order.duration_days = 0
        slot_order.amount_rub = Decimal("100.00")
        from datetime import timedelta
        t0 = now_utc()
        slot_order.paid_at = t0
        slot_order.created_at = t0
        slot_order.user = user

        quota_order = MagicMock()
        quota_order.id = uuid.uuid4()
        quota_order.service_type = "white_internet"
        quota_order.payment_method = "yookassa"
        quota_order.tariff = None
        quota_order.metadata_ = {"operation": "topup"}
        quota_order.device_limit = 2
        quota_order.traffic_bytes = 20 * (1024**3)
        quota_order.duration_days = 0
        quota_order.amount_rub = Decimal("150.00")
        quota_order.paid_at = t0 - timedelta(minutes=1)
        quota_order.created_at = t0 - timedelta(minutes=1)
        quota_order.user = user

        res_orders = MagicMock()
        res_orders.scalars().all.return_value = [slot_order, quota_order]

        res_audit = MagicMock()
        res_audit.scalars().all.return_value = []

        session.execute.side_effect = [res_orders, res_audit]

        entries, total = await get_purchase_logs_paginated(session, page=1, per_page=10)
        self.assertEqual(len(entries), 2)
        # Check order query statement excludes topup
        called_stmt = session.execute.call_args_list[0][0][0]
        stmt_sql = str(called_stmt)
        self.assertIn("orders.service_type IN", stmt_sql)

        # Slot order checks: real-world scenario where user had 2 devices and added 1 (limit becomes 3)
        self.assertEqual(entries[0].numeric_id, str(slot_order.id)[:8])
        self.assertEqual(entries[0].operation_type, "add_device_slot")
        self.assertEqual(entries[0].operation_title, "Доп. устройство")
        self.assertEqual(entries[0].tariff_name, "+1 слот")
        self.assertEqual(entries[0].device_limit, 3)
        self.assertEqual(entries[0].payment_method, "wallet")

        # Quota order checks
        self.assertEqual(entries[1].numeric_id, str(quota_order.id)[:8])
        self.assertEqual(entries[1].operation_type, "topup_quota")
        self.assertEqual(entries[1].operation_title, "Докупка трафика")
        self.assertEqual(entries[1].tariff_name, "+20 ГБ")
        self.assertEqual(entries[1].payment_method, "yookassa")

    async def test_purchases_repo_resolves_awg_renewal(self):
        import uuid
        from database.models import Tariff

        session = AsyncMock()
        user = User(id=16, telegram_id=3006, username="awg_renewer")
        tariff = Tariff(id=5, name="Базовый 30 дней", duration_days=30, price_rub=150)

        renew_order = MagicMock()
        renew_order.id = uuid.uuid4()
        renew_order.service_type = "awg"
        renew_order.payment_method = "wallet"
        renew_order.tariff = tariff
        renew_order.metadata_ = {"operation": "renew", "is_renewal": True, "tariff_name": "Базовый 30 дней"}
        renew_order.device_limit = 2
        renew_order.traffic_bytes = 0
        renew_order.duration_days = 30
        renew_order.amount_rub = Decimal("150.00")
        t0 = now_utc()
        renew_order.paid_at = t0
        renew_order.created_at = t0
        renew_order.user = user

        res_orders = MagicMock()
        res_orders.scalars().all.return_value = [renew_order]
        res_audit = MagicMock()
        res_audit.scalars().all.return_value = []
        session.execute.side_effect = [res_orders, res_audit]

        entries, total = await get_purchase_logs_paginated(session, page=1, per_page=10)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].operation_type, "renew")
        self.assertEqual(entries[0].operation_title, "Продление")
        self.assertEqual(entries[0].tariff_name, "Базовый 30 дней")

    async def test_get_purchase_log_by_id_excludes_topup_and_unpaid(self):
        import uuid

        session = AsyncMock()
        res_order = MagicMock()
        res_order.scalar_one_or_none.return_value = None
        session.execute.return_value = res_order

        test_uuid = uuid.uuid4()
        entry = await get_purchase_log_by_id(session, f"order_{test_uuid}")
        self.assertIsNone(entry)

        # Check statement requires paid status and non-topup service type
        called_stmt = session.execute.call_args_list[0][0][0]
        stmt_sql = str(called_stmt)
        self.assertIn("orders.status = :status_1", stmt_sql)
        self.assertIn("orders.service_type IN", stmt_sql)

    async def test_show_order_card_yookassa_pending_diagnostics(self):
        import uuid
        from datetime import datetime, timedelta, timezone
        from bot.handlers.admin.payments import show_order_card
        from database.models import Order

        session = AsyncMock()
        test_uuid = uuid.uuid4()
        user = User(id=20, telegram_id=555666, username="diagnostics_user")
        old_created = datetime.now(timezone.utc) - timedelta(minutes=25)
        order = Order(
            id=test_uuid,
            user_id=20,
            service_type="topup",
            amount_rub=Decimal("300.00"),
            duration_days=0,
            status="pending",
            payment_method="yookassa",
            payment_url="https://yookassa.ru/checkout/12345",
            created_at=old_created,
            user=user,
            tariff=None,
            metadata_={"payment_creation_ambiguous": True},
        )
        session.scalar.return_value = order

        callback = AsyncMock()
        callback.data = f"admin_order_card:{test_uuid}"
        callback.from_user.id = 12345
        callback.message = AsyncMock()
        state = AsyncMock()

        with patch("bot.handlers.admin.payments.is_admin", return_value=True):
            await show_order_card(callback, state, session)

        callback.message.edit_text.assert_called_once()
        rendered_text = callback.message.edit_text.call_args[0][0]
        self.assertIn("ЮKassa", rendered_text)
        self.assertIn("Заказ #", rendered_text)
        self.assertIn("Пополнение баланса (topup)", rendered_text)
        self.assertIn("Ожидает завершения оплаты клиентом", rendered_text)
        self.assertIn("Тайм-аут шлюза при создании", rendered_text)
        self.assertIn("https://yookassa.ru/checkout/12345", rendered_text)

    async def test_show_order_card_displays_wallet_order(self):
        import uuid
        from bot.handlers.admin.payments import show_order_card
        from database.models import Order, Tariff

        session = AsyncMock()
        test_uuid = uuid.uuid4()
        user = User(id=20, telegram_id=555666, username="wallet_user")
        tariff = Tariff(id=1, name="Базовый 30 дней")
        order = Order(
            id=test_uuid,
            user_id=20,
            service_type="awg",
            amount_rub=Decimal("150.00"),
            duration_days=30,
            status="paid",
            payment_method="wallet",
            created_at=now_utc(),
            paid_at=now_utc(),
            user=user,
            tariff=tariff,
        )
        session.scalar.return_value = order

        callback = AsyncMock()
        callback.data = f"admin_order_card:{test_uuid}"
        callback.from_user.id = 12345
        callback.message = AsyncMock()
        state = AsyncMock()

        with patch("bot.handlers.admin.payments.is_admin", return_value=True):
            await show_order_card(callback, state, session)

        callback.message.edit_text.assert_called_once()
        rendered_text = callback.message.edit_text.call_args[0][0]
        self.assertIn("Заказ #", rendered_text)
        self.assertIn("Способ оплаты:", rendered_text)
        self.assertNotIn("Шлюз:", rendered_text)
        self.assertIn("Внутренний баланс", rendered_text)
        self.assertIn("150 ₽", rendered_text)
        self.assertIn("Базовый 30 дней", rendered_text)

        reply_markup = callback.message.edit_text.call_args[1]["reply_markup"]
        cb_datas = [btn.callback_data for row in reply_markup.inline_keyboard for btn in row]
        self.assertIn("admin_user_card:555666", cb_datas)
        self.assertIn("admin_payments_filter:user:555666:1", cb_datas)
        self.assertIn("admin_payments", cb_datas)

    async def test_show_order_card_displays_sbp_order(self):
        import uuid
        from bot.handlers.admin.payments import show_order_card
        from database.models import Order, Tariff

        session = AsyncMock()
        test_uuid = uuid.uuid4()
        user = User(id=21, telegram_id=777888, username="sbp_user")
        tariff = Tariff(id=2, name="Премиум 30 дней")
        order = Order(
            id=test_uuid,
            user_id=21,
            service_type="awg",
            amount_rub=Decimal("300.00"),
            duration_days=30,
            status="paid",
            payment_method="sbp",
            created_at=now_utc(),
            paid_at=now_utc(),
            user=user,
            tariff=tariff,
        )
        session.scalar.return_value = order

        callback = AsyncMock()
        callback.data = f"admin_order_card:{test_uuid}"
        callback.from_user.id = 12345
        callback.message = AsyncMock()
        state = AsyncMock()

        with patch("bot.handlers.admin.payments.is_admin", return_value=True):
            await show_order_card(callback, state, session)

        callback.message.edit_text.assert_called_once()
        rendered_text = callback.message.edit_text.call_args[0][0]
        self.assertIn("Способ оплаты:", rendered_text)
        self.assertIn("СБП", rendered_text)

    async def test_show_order_card_not_found_alert(self):
        import uuid
        from bot.handlers.admin.payments import show_order_card
        from bot import texts

        session = AsyncMock()
        session.scalar.return_value = None

        test_uuid = uuid.uuid4()
        callback = AsyncMock()
        callback.data = f"admin_order_card:{test_uuid}"
        callback.from_user.id = 12345
        callback.message = AsyncMock()
        state = AsyncMock()

        with patch("bot.handlers.admin.payments.is_admin", return_value=True):
            await show_order_card(callback, state, session)

        callback.answer.assert_called_once_with(
            texts.ADMIN_PAYMENT_NOT_FOUND_ALERT, show_alert=True
        )
        callback.message.edit_text.assert_not_called()

    async def test_show_user_payments_list_includes_wallet_and_yookassa_orders(self):
        import uuid
        from bot.handlers.admin.payments import show_user_payments_list
        from database.models import Order

        session = AsyncMock()
        user = User(id=20, telegram_id=555666, username="multi_order_user")

        order_yoo = Order(
            id=uuid.uuid4(),
            user_id=20,
            service_type="topup",
            amount_rub=Decimal("300.00"),
            status="paid",
            payment_method="yookassa",
            created_at=now_utc(),
            user=user,
        )
        order_wallet = Order(
            id=uuid.uuid4(),
            user_id=20,
            service_type="awg",
            amount_rub=Decimal("150.00"),
            status="paid",
            payment_method="wallet",
            created_at=now_utc(),
            user=user,
        )

        session.scalar.side_effect = [2, 0]

        res_orders = MagicMock()
        res_orders.scalars().all.return_value = [order_yoo, order_wallet]
        res_legacy = MagicMock()
        res_legacy.scalars().all.return_value = []
        session.execute.side_effect = [res_orders, res_legacy]

        callback = AsyncMock()
        callback.data = f"admin_payments_filter:user:{user.telegram_id}:1"
        callback.from_user.id = 12345
        callback.message = AsyncMock()
        state = AsyncMock()

        with patch("bot.handlers.admin.payments.is_admin", return_value=True), \
             patch("database.repositories.users_repo.get_user_by_telegram_id", return_value=user):
            await show_user_payments_list(callback, state, session)

        # Verify that both count and order queries do not filter by payment_method
        count_stmt = session.scalar.call_args_list[0][0][0]
        compiled_count = str(count_stmt.compile(compile_kwargs={"literal_binds": True}))
        self.assertNotIn("payment_method", compiled_count)

        order_stmt = session.execute.call_args_list[0][0][0]
        compiled_orders = str(order_stmt.compile(compile_kwargs={"literal_binds": True}))
        self.assertNotIn("payment_method", compiled_orders)

        callback.message.edit_text.assert_called_once()
        rendered_text = callback.message.edit_text.call_args[0][0]
        self.assertIn("Платежи пользователя", rendered_text)
        self.assertNotIn("через ЮKassa", rendered_text)

        reply_markup = callback.message.edit_text.call_args[1]["reply_markup"]
        cb_datas = [btn.callback_data for row in reply_markup.inline_keyboard for btn in row]
        self.assertIn(f"admin_order_card:{order_yoo.id}", cb_datas)
        self.assertIn(f"admin_order_card:{order_wallet.id}", cb_datas)


if __name__ == "__main__":
    unittest.main()
