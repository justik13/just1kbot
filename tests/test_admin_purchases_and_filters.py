import unittest
from datetime import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from database.models import (
    AuditLog,
    Tariff,
    TariffQuote,
    TariffVersion,
    User,
)
from database.repositories.purchases_repo import (
    PurchaseLogEntry,
    get_purchase_log_by_id,
    get_purchase_logs_paginated,
)
from database.repositories.users_repo import (
    get_filtered_users_count,
    get_filtered_users_paginated,
)
from bot.handlers.admin.users.common import _build_users_list_text_and_kb
from bot.keyboards.admin.dashboard import get_admin_menu
from bot.keyboards.device import get_device_keyboard
from utils.datetime_helpers import now_utc


class AdminPurchasesAndFiltersTests(unittest.IsolatedAsyncioTestCase):

    def test_admin_dashboard_keyboard_has_purchases(self):
        markup = get_admin_menu()
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
        self.assertIn("support_help", all_callbacks)

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
        self.assertIn("admin_users_filter:banned:none:1", all_callbacks)
        self.assertIn("admin_users_filter_menu:server", all_callbacks)
        self.assertIn("admin_users_filter_menu:tariff", all_callbacks)

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
        ver = TariffVersion(
            id=1,
            tariff_id=1,
            version_number=1,
            name_snapshot="Test Tariff 30d",
            duration_hours=720,
            device_limit=2,
            price_rub=Decimal("199.00"),
            tariff=tariff,
        )

        import uuid
        now = now_utc()
        quote = TariffQuote(
            id=100,
            public_id=uuid.uuid4(),
            user_id=10,
            user=user,
            operation_type="purchase",
            target_tariff_version_id=1,
            target_tariff_version=ver,
            amount_due_rub=Decimal("199.00"),
            status="consumed",
            consumed_at=now,
            created_at=now,
        )

        audit_log = AuditLog(
            id=200,
            admin_id=872658825,
            action="ADMIN_SUB_GRANT",
            target_type="User",
            target_id=10,
            details="days=30",
            created_at=now,
        )

        res_quote = MagicMock()
        res_quote.scalars().all.return_value = [quote]

        res_audit = MagicMock()
        res_audit.scalars().all.return_value = [audit_log]

        res_users = MagicMock()
        res_users.all.return_value = [user]

        session.execute.side_effect = [res_quote, res_audit]
        session.scalars.return_value = res_users

        entries, total = await get_purchase_logs_paginated(session, page=1, per_page=10)
        self.assertEqual(total, 2)
        self.assertEqual(entries[0].id, "quote_100")
        self.assertEqual(entries[0].amount_rub, Decimal("199.00"))
        self.assertEqual(entries[0].tariff_name, "Test Tariff 30d")
        self.assertEqual(entries[1].id, "audit_200")


if __name__ == "__main__":
    unittest.main()
