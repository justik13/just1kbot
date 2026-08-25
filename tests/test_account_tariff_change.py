import inspect
import unittest
import uuid
from types import SimpleNamespace

from bot.keyboards.payment import (
    get_balance_change_confirm_keyboard,
    get_balance_change_shortage_keyboard,
    get_balance_change_start_keyboard,
    get_change_tariff_keyboard,
    get_same_tariff_keyboard,
)
from database.models import EntitlementEntry
from services import account_tariff_change


def callbacks(markup):
    return [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data
    ]


class AccountTariffChangeContractTests(unittest.IsolatedAsyncioTestCase):
    def test_change_requires_two_distinct_actions(self):
        quote_id = str(uuid.uuid4())
        start = callbacks(
            get_balance_change_start_keyboard(
                quote_id, "payment_change_tariff"
            )
        )
        confirm = callbacks(
            get_balance_change_confirm_keyboard(
                quote_id, "payment_change_tariff"
            )
        )
        self.assertEqual(start[0], f"balance_change_review:{quote_id}")
        self.assertEqual(confirm[0], f"balance_change_confirm:{quote_id}")
        self.assertNotEqual(start[0], confirm[0])
        self.assertTrue(all(len(item.encode()) <= 64 for item in start + confirm))

    def test_current_tariff_is_excluded_and_stale_path_leads_to_renew(self):
        tariffs = [
            SimpleNamespace(id=1, device_limit=2),
            SimpleNamespace(id=2, device_limit=5),
        ]
        values = callbacks(
            get_change_tariff_keyboard(
                tariffs,
                2,
                is_subscription_active=True,
                current_tariff_id=1,
            )
        )
        self.assertNotIn("select_tariff:1:change", values)
        self.assertIn("select_tariff:2:change", values)
        self.assertEqual(
            callbacks(get_same_tariff_keyboard())[0], "payment_quick_renew"
        )

    def test_change_shortage_has_exact_and_custom_topups(self):
        quote_id = str(uuid.uuid4())
        values = callbacks(
            get_balance_change_shortage_keyboard(
                quote_id, 90, "payment_change_tariff"
            )
        )
        self.assertEqual(
            values[:2],
            [
                f"bal_chg_short_exact:{quote_id}",
                f"bal_chg_short_custom:{quote_id}",
            ],
        )

    def test_settlement_owns_no_transaction_or_provider_http(self):
        source = inspect.getsource(account_tariff_change)
        self.assertNotIn(".commit(", source)
        self.assertNotIn("YooKassa", source)
        self.assertIn("session.begin_nested()", source)
        fingerprint = source.index("balance_snapshot_fingerprint(")
        debit = source.index("create_purchase_debit(", fingerprint)
        conversion = source.index("get_or_create_conversion_entry(", debit)
        activation = source.index("SubscriptionService.replace_subscription(")
        consumed = source.index('quote.status = "consumed"')
        self.assertLess(fingerprint, debit)
        self.assertLess(debit, conversion)
        self.assertLess(conversion, activation)
        self.assertLess(activation, consumed)

    def test_entitlement_schema_preserves_exact_change_hours(self):
        constraint = next(
            item
            for item in EntitlementEntry.__table__.constraints
            if item.name == "ck_entitlement_entries_type"
        )
        self.assertIn("tariff_change", str(constraint.sqltext))
        self.assertIn("hours_delta", EntitlementEntry.__table__.c)

    async def test_same_device_limit_tariff_change_rejected(self):
        from unittest.mock import AsyncMock, MagicMock, patch
        from datetime import datetime, timezone
        from database.models import User, Tariff
        from services.tariff_change_quote import create_tariff_change_quote

        user = User(
            id=1,
            telegram_id=123,
            device_limit=2,
            current_tariff_id=1,
            subscription_end=datetime(2027, 1, 1, tzinfo=timezone.utc),
            financial_hold=False,
            is_deleted=False,
            is_banned=False,
            is_bot_blocked=False,
        )
        source_tariff = Tariff(id=1, name="Базовый 7d", device_limit=2, duration_days=7, price_rub=35, is_active=True)
        target_tariff = Tariff(id=3, name="Базовый 90d", device_limit=2, duration_days=90, price_rub=240, is_active=True)

        session = AsyncMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [source_tariff, target_tariff]
        session.scalars = AsyncMock(return_value=mock_scalars)

        with patch("services.tariff_change_quote.lock_checkout_user", AsyncMock(return_value=user)), \
             patch("services.tariff_change_quote.get_account_balance", AsyncMock(return_value=SimpleNamespace(debt=0))), \
             patch("services.tariff_change_quote.get_active_financial_quotes_for_update", AsyncMock(return_value=[])):

            res = await create_tariff_change_quote(
                session,
                user_id=1,
                target_tariff_id=3,
                as_of=datetime(2026, 8, 25, tzinfo=timezone.utc),
            )
            self.assertEqual(res.failure_code, "same_tariff_requires_renew")


if __name__ == "__main__":
    unittest.main()

