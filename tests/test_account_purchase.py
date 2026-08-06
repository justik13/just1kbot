import inspect
import unittest
import uuid

from bot.keyboards.payment import (
    get_balance_purchase_confirm_keyboard,
    get_balance_purchase_start_keyboard,
    get_balance_shortage_keyboard,
    get_topup_credit_keyboard,
)
from database.models import EntitlementEntry, PaidValueLedgerEntry
from services import account_purchase


def callbacks(markup):
    return [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data
    ]


class AccountPurchaseContractTests(unittest.TestCase):
    def test_purchase_requires_two_distinct_user_actions(self):
        quote_id = str(uuid.uuid4())
        start = callbacks(
            get_balance_purchase_start_keyboard(quote_id, "payment_showcase")
        )
        confirm = callbacks(
            get_balance_purchase_confirm_keyboard(quote_id, "payment_showcase")
        )
        self.assertEqual(start[0], f"balance_purchase_review:{quote_id}")
        self.assertEqual(confirm[0], f"balance_purchase_confirm:{quote_id}")
        self.assertNotEqual(start[0], confirm[0])
        self.assertTrue(all(len(item.encode()) <= 64 for item in start + confirm))

    def test_shortage_flow_has_exact_and_custom_options(self):
        quote_id = str(uuid.uuid4())
        self.assertEqual(
            callbacks(
                get_balance_shortage_keyboard(
                    quote_id, 200, "payment_showcase"
                )
            )[:2],
            [
                f"bal_short_exact:{quote_id}",
                f"bal_short_custom:{quote_id}",
            ],
        )

    def test_topup_credit_can_resume_but_never_auto_purchases(self):
        markup = get_topup_credit_keyboard(
            {"tariff_id": 7, "source": "showcase"}
        )
        self.assertEqual(
            callbacks(markup),
            ["balance_resume_purchase:7:showcase", "menu_balance"],
        )
        self.assertNotIn("balance_purchase_confirm", repr(markup))

    def test_quote_backed_economic_types_are_in_metadata(self):
        paid_constraint = next(
            item
            for item in PaidValueLedgerEntry.__table__.constraints
            if item.name == "ck_paid_value_ledger_entry_type"
        )
        entitlement_constraint = next(
            item
            for item in EntitlementEntry.__table__.constraints
            if item.name == "ck_entitlement_entries_type"
        )
        self.assertIn("account_purchase", str(paid_constraint.sqltext))
        self.assertIn(
            "account_purchase_grant", str(entitlement_constraint.sqltext)
        )

    def test_financial_settlement_contains_no_provider_http(self):
        source = inspect.getsource(account_purchase)
        self.assertNotIn("YooKassa", source)
        self.assertIn("session.begin_nested()", source)
        debit = source.index("create_purchase_debit(")
        entitlement = source.index("_get_or_create_entitlement(", debit)
        activation = source.index("SubscriptionService.extend_subscription(", debit)
        consumed = source.index('quote.status = "consumed"', debit)
        self.assertLess(debit, entitlement)
        self.assertLess(entitlement, activation)
        self.assertLess(activation, consumed)


if __name__ == "__main__":
    unittest.main()
