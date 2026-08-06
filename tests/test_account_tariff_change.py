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


class AccountTariffChangeContractTests(unittest.TestCase):
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
        self.assertNotIn("select_tariff_type:2:change", values)
        self.assertIn("select_tariff_type:5:change", values)
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


if __name__ == "__main__":
    unittest.main()
