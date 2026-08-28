"""Unit tests asserting Single Source of Truth invariants for domain enums and models."""
from decimal import Decimal
import unittest

import bot.constants
import config.constants
import config.enums
from database import models


class DomainEnumsSSOTTests(unittest.TestCase):
    """Verify that domain enums in config.enums strictly match model constants and DB constraints."""

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
            models.ACCOUNT_LEDGER_ENTRY_TYPES,
            tuple(s.value for s in config.enums.AccountLedgerEntryType),
        )

    def test_strenum_string_interoperability(self):
        """StrEnums must behave identically to raw strings in equality, hashing, and formatting."""
        self.assertEqual(config.enums.PaymentProviderStatus.SUCCEEDED, "succeeded")
        self.assertEqual(config.enums.PaymentFulfillmentStatus.SUCCEEDED, "succeeded")
        self.assertEqual(config.enums.TariffQuoteOperation.PURCHASE, "purchase")
        self.assertEqual(config.enums.TariffQuoteStatus.ACTIVE, "active")
        self.assertEqual(config.enums.ServerHealthState.ONLINE, "ONLINE")

        test_dict = {config.enums.PaymentProviderStatus.PENDING: "ok"}
        self.assertEqual(test_dict.get("pending"), "ok")

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


if __name__ == "__main__":
    unittest.main()
