import re
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
import unittest
from sqlalchemy import CheckConstraint

import bot.constants
import config.constants
import config.enums
import config.tariffs
from database import models


def _extract_check_constraint_in(table, constraint_name: str) -> set[str]:
    for constraint in table.constraints:
        if isinstance(constraint, CheckConstraint) and constraint.name == constraint_name:
            sql_text = str(constraint.sqltext)
            match = re.search(r"\bIN\s*\(([^)]+)\)", sql_text, re.IGNORECASE)
            if match:
                raw_items = match.group(1).split(",")
                return {item.strip().strip("'\"") for item in raw_items}
    raise AssertionError(f"CheckConstraint '{constraint_name}' not found on table {table.name}")


class DomainEnumsSSOTTests(unittest.TestCase):
    """Verify that domain enums in config.enums strictly match model constants, DB constraints, and architecture rules."""

    def test_enums_exported_in_constants(self):
        """All Enums in config.enums must be re-exported in config.constants and bot.constants."""
        for enum_name in config.enums.__all__:
            self.assertTrue(
                hasattr(config.constants, enum_name),
                f"{enum_name} missing from config.constants",
            )
            self.assertTrue(
                hasattr(bot.constants, enum_name),
                f"{enum_name} missing from bot.constants",
            )
            self.assertIs(
                getattr(config.constants, enum_name),
                getattr(config.enums, enum_name),
            )
            self.assertIs(
                getattr(bot.constants, enum_name),
                getattr(config.enums, enum_name),
            )

    def test_all_enums_declared_are_strenums_with_values(self):
        """Every exported enum in config.enums must be a valid StrEnum with non-empty members."""
        self.assertEqual(len(config.enums.__all__), 20)
        for enum_name in config.enums.__all__:
            enum_cls = getattr(config.enums, enum_name)
            self.assertTrue(
                issubclass(enum_cls, (str, StrEnum)),
                f"{enum_name} is not a StrEnum subclass",
            )
            members = list(enum_cls)
            self.assertGreater(len(members), 0, f"{enum_name} has no members")
            for member in members:
                self.assertIsInstance(member.value, str)
                self.assertTrue(len(member.value) > 0)
                # StrEnum member must equal its string value
                self.assertEqual(member, member.value)
                self.assertEqual(enum_cls(member.value), member)

    def test_model_tuples_derived_from_enums(self):
        """database.models tuple constants must match exactly the values from config.enums."""
        self.assertEqual(
            models.API_OPERATION_TYPES,
            tuple(s.value for s in config.enums.ApiOperationType),
        )
        self.assertEqual(
            models.API_OPERATION_STATUSES,
            tuple(s.value for s in config.enums.ApiOperationStatus),
        )
        self.assertEqual(
            models.PAYMENT_PROVIDER_STATUSES,
            tuple(s.value for s in config.enums.PaymentProviderStatus),
        )
        self.assertEqual(
            models.PAYMENT_FULFILLMENT_STATUSES,
            tuple(s.value for s in config.enums.PaymentFulfillmentStatus),
        )
        self.assertEqual(
            models.PAYMENT_RECONCILIATION_STATUSES,
            tuple(s.value for s in config.enums.PaymentReconciliationStatus),
        )
        self.assertEqual(
            models.PAYMENT_QUEUE_STATUSES,
            tuple(s.value for s in config.enums.PaymentQueueStatus),
        )
        self.assertEqual(
            models.ACCOUNT_LEDGER_ENTRY_TYPES,
            tuple(s.value for s in config.enums.AccountLedgerEntryType),
        )
        self.assertEqual(
            models.ACCOUNT_RESERVATION_TYPES,
            tuple(s.value for s in config.enums.AccountReservationType),
        )
        self.assertEqual(
            models.ACCOUNT_RESERVATION_STATUSES,
            tuple(s.value for s in config.enums.AccountReservationStatus),
        )
        self.assertEqual(
            models.PAID_VALUE_ENTRY_TYPES,
            tuple(s.value for s in config.enums.PaidValueEntryType),
        )
        self.assertEqual(
            models.ENTITLEMENT_ENTRY_TYPES,
            tuple(s.value for s in config.enums.EntitlementEntryType),
        )
        self.assertEqual(
            models.TARIFF_QUOTE_OPERATIONS,
            tuple(s.value for s in config.enums.TariffQuoteOperation),
        )
        self.assertEqual(
            models.TARIFF_QUOTE_STATUSES,
            tuple(s.value for s in config.enums.TariffQuoteStatus),
        )
        self.assertEqual(
            models.VPN_PROVISIONING_STATUSES,
            tuple(s.value for s in config.enums.VPNProvisioningStatus),
        )
        self.assertEqual(
            models.WEBHOOK_INBOX_STATUSES,
            tuple(s.value for s in config.enums.WebhookInboxStatus),
        )
        self.assertEqual(
            models.PAYMENT_DISPUTE_STATUSES,
            tuple(s.value for s in config.enums.PaymentDisputeStatus),
        )
        self.assertEqual(
            models.PAYMENT_CHECKOUT_STATUSES,
            tuple(s.value for s in config.enums.PaymentCheckoutStatus),
        )

    def test_database_model_constraints_match_enums(self):
        """Extract CheckConstraint IN (...) definitions on SQLAlchemy models and ensure 0 drift against Enums."""
        # 1. VPNProfile provisioning_status
        self.assertEqual(
            _extract_check_constraint_in(models.VPNProfile.__table__, "ck_vpn_profiles_provisioning_status"),
            set(config.enums.VPNProvisioningStatus),
        )

        # 2. TariffQuote operation & status
        self.assertEqual(
            _extract_check_constraint_in(models.TariffQuote.__table__, "ck_tariff_quotes_operation"),
            set(config.enums.TariffQuoteOperation),
        )
        self.assertEqual(
            _extract_check_constraint_in(models.TariffQuote.__table__, "ck_tariff_quotes_status"),
            set(config.enums.TariffQuoteStatus),
        )

        # 3. PaidValueLedgerEntry entry_type
        self.assertEqual(
            _extract_check_constraint_in(models.PaidValueLedgerEntry.__table__, "ck_paid_value_ledger_entry_type"),
            set(config.enums.PaidValueEntryType),
        )

        # 4. Payment provider_status, fulfillment_status, reconciliation_status, checkout_status
        self.assertEqual(
            _extract_check_constraint_in(models.Payment.__table__, "ck_payments_provider_status"),
            set(config.enums.PaymentProviderStatus),
        )
        self.assertEqual(
            _extract_check_constraint_in(models.Payment.__table__, "ck_payments_fulfillment_status"),
            set(config.enums.PaymentFulfillmentStatus),
        )
        self.assertEqual(
            _extract_check_constraint_in(models.Payment.__table__, "ck_payments_reconciliation_status"),
            set(config.enums.PaymentReconciliationStatus),
        )
        self.assertEqual(
            _extract_check_constraint_in(models.Payment.__table__, "ck_payments_checkout_status"),
            set(config.enums.PaymentCheckoutStatus),
        )

        # 5. AccountLedgerEntry entry_type
        self.assertEqual(
            _extract_check_constraint_in(models.AccountLedgerEntry.__table__, "ck_account_ledger_entry_type"),
            set(config.enums.AccountLedgerEntryType),
        )

        # 6. AccountBalanceReservation reservation_type & status
        self.assertEqual(
            _extract_check_constraint_in(models.AccountBalanceReservation.__table__, "ck_account_reservations_type"),
            set(config.enums.AccountReservationType),
        )
        self.assertEqual(
            _extract_check_constraint_in(models.AccountBalanceReservation.__table__, "ck_account_reservations_status"),
            set(config.enums.AccountReservationStatus),
        )

        # 7. WebhookInbox status
        self.assertEqual(
            _extract_check_constraint_in(models.WebhookInbox.__table__, "ck_webhook_inbox_status"),
            set(config.enums.WebhookInboxStatus),
        )

        # 8. EntitlementEntry entry_type
        self.assertEqual(
            _extract_check_constraint_in(models.EntitlementEntry.__table__, "ck_entitlement_entries_type"),
            set(config.enums.EntitlementEntryType),
        )

        # 9. PaymentProviderOperation status
        self.assertEqual(
            _extract_check_constraint_in(models.PaymentProviderOperation.__table__, "ck_payment_provider_operations_status"),
            set(config.enums.PaymentQueueStatus),
        )

        # 10. APIOperation operation_type & status
        self.assertEqual(
            _extract_check_constraint_in(models.APIOperation.__table__, "ck_api_operations_operation_type"),
            set(config.enums.ApiOperationType),
        )
        self.assertEqual(
            _extract_check_constraint_in(models.APIOperation.__table__, "ck_api_operations_status"),
            set(config.enums.ApiOperationStatus),
        )

        # 11. ProviderRefundOperation status
        from database.refund_models import ProviderRefundOperation
        self.assertEqual(
            _extract_check_constraint_in(ProviderRefundOperation.__table__, "ck_provider_refund_operations_status"),
            set(config.enums.ProviderRefundOperationStatus),
        )

    def test_exact_spelling_and_serialization_integrity(self):
        """Ensure bit-for-bit spelling fidelity for legacy and provider DB compatibility."""
        # YooKassa / DB uses 'canceled' (single 'l') for Payment Provider Status
        self.assertEqual(config.enums.PaymentProviderStatus.CANCELED, "canceled")
        self.assertEqual(config.enums.PaymentProviderStatus("canceled"), config.enums.PaymentProviderStatus.CANCELED)

        # Quotes use 'cancelled' (double 'l')
        self.assertEqual(config.enums.TariffQuoteStatus.CANCELLED, "cancelled")
        self.assertEqual(config.enums.TariffQuoteStatus("cancelled"), config.enums.TariffQuoteStatus.CANCELLED)

        # ApiOperation and PaymentQueue use 'cancelled' (double 'l')
        self.assertEqual(config.enums.ApiOperationStatus.CANCELLED, "cancelled")
        self.assertEqual(config.enums.PaymentQueueStatus.CANCELLED, "cancelled")

    def test_default_tariffs_seeds_ssot_layering(self):
        """DEFAULT_TARIFFS_SEEDS must live strictly in config.tariffs and NOT in bot.texts."""
        from bot import texts

        self.assertFalse(
            hasattr(texts, "DEFAULT_TARIFFS_SEEDS"),
            "DEFAULT_TARIFFS_SEEDS must not be in bot.texts (Level 0 domain seed SSOT)",
        )
        self.assertTrue(
            hasattr(config.tariffs, "DEFAULT_TARIFFS_SEEDS"),
            "DEFAULT_TARIFFS_SEEDS must be in config.tariffs",
        )
        seeds = config.tariffs.DEFAULT_TARIFFS_SEEDS
        self.assertIsInstance(seeds, list)
        self.assertGreater(len(seeds), 0)
        for seed in seeds:
            self.assertIn("name", seed)
            self.assertIn("duration_days", seed)
            self.assertIn("device_limit", seed)
            self.assertIn("price_rub", seed)
            self.assertIn("sort_order", seed)

    def test_tariff_version_duration_days_property(self):
        """TariffVersion duration_days property returns duration_hours // 24."""
        version = models.TariffVersion(
            id=1,
            tariff_id=10,
            version_number=1,
            name_snapshot="Basic",
            duration_hours=720,
            device_limit=2,
            price_rub=Decimal("300.00"),
        )
        self.assertEqual(version.duration_days, 30)

        version_half = models.TariffVersion(
            id=2,
            tariff_id=10,
            version_number=2,
            name_snapshot="Basic 15d",
            duration_hours=360,
            device_limit=2,
            price_rub=Decimal("150.00"),
        )
        self.assertEqual(version_half.duration_days, 15)

    def test_support_urls_in_texts_ssot(self):
        """Amnezia support URLs are correctly exposed via bot.texts."""
        from bot import texts

        self.assertTrue(texts.AMNEZIA_DOWNLOAD_MIRROR.startswith("https://"))
        self.assertTrue(texts.AMNEZIA_GITHUB_LATEST.startswith("https://"))
        self.assertTrue(texts.AMNEZIA_OFFICIAL_SITE.startswith("https://"))
        self.assertTrue(texts.AMNEZIA_DOCS.startswith("https://"))
        self.assertTrue(texts.AMNEZIA_SPLIT_TUNNELING.startswith("https://"))
        self.assertTrue(texts.AMNEZIA_IOS_RU.startswith("https://"))
        self.assertTrue(texts.AMNEZIA_WIN_INSTALL.startswith("https://"))
        self.assertTrue(texts.AMNEZIA_WIN_UPDATE.startswith("https://"))

    def test_no_active_code_references_to_legacy_incy_or_bridge(self):
        """Active python source files must contain zero references to deprecated INCY or AMNEZIA_BRIDGE_HMAC_SECRET."""
        root = Path(__file__).parents[1]
        active_dirs = [root / "bot", root / "config", root / "database", root / "services", root / "utils"]

        for d in active_dirs:
            for py_file in d.rglob("*.py"):
                content = py_file.read_text(encoding="utf-8")
                self.assertNotIn(
                    "AMNEZIA_BRIDGE",
                    content,
                    f"Found legacy AMNEZIA_BRIDGE reference in {py_file}",
                )
                self.assertNotIn(
                    "incy",
                    content.lower(),
                    f"Found legacy INCY reference in {py_file}",
                )


if __name__ == "__main__":
    unittest.main()
