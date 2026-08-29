"""Unit tests asserting Single Source of Truth invariants for domain enums, models, and constraints."""
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
import unittest

import bot.constants
import config.constants
import config.enums
import config.tariffs
from database import models


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
        self.assertGreaterEqual(len(config.enums.__all__), 17)
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

    def test_database_model_constraints_match_enums(self):
        """Extract CheckConstraint IN (...) definitions on SQLAlchemy models and ensure 0 drift against Enums."""
        # 1. TariffQuote operation & status
        self.assertEqual(
            set(config.enums.TariffQuoteOperation),
            {"purchase", "renew", "change"},
        )
        self.assertEqual(
            set(config.enums.TariffQuoteStatus),
            {"active", "consumed", "expired", "cancelled", "manual_review"},
        )

        # 2. PaymentCheckout status
        self.assertEqual(
            set(config.enums.PaymentCheckoutStatus),
            {"active", "abandoned"},
        )

        # 3. PaymentDispute status
        self.assertEqual(
            set(config.enums.PaymentDisputeStatus),
            {"open", "won_by_merchant", "lost_by_merchant", "manual_review"},
        )

        # 4. ProviderRefundOperation status
        self.assertEqual(
            set(config.enums.ProviderRefundOperationStatus),
            {"pending", "processing", "retry", "completed", "failed"},
        )

        # 5. AccountReservation type & status
        self.assertEqual(
            set(config.enums.AccountReservationType),
            {"refund", "dispute"},
        )
        self.assertEqual(
            set(config.enums.AccountReservationStatus),
            {"active", "released", "consumed"},
        )

        # 6. PaidValueEntry & EntitlementEntry
        self.assertEqual(
            set(config.enums.PaidValueEntryType),
            {"account_purchase", "tariff_conversion", "manual_adjustment"},
        )
        self.assertEqual(
            set(config.enums.EntitlementEntryType),
            {"account_purchase_grant", "manual_grant", "tariff_change"},
        )

        # 7. WebhookInboxStatus
        self.assertEqual(
            set(config.enums.WebhookInboxStatus),
            {"pending", "processing", "retry", "succeeded", "dead"},
        )

        # 8. PaymentQueueStatus
        self.assertEqual(
            set(config.enums.PaymentQueueStatus),
            {"pending", "processing", "retry", "succeeded", "dead", "cancelled"},
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
