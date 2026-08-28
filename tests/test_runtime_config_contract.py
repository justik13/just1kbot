import os
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from config.settings import Settings

ROOT = Path(__file__).resolve().parents[1]

BASE = {
    "BOT_TOKEN": "123456:TEST_TOKEN",
    "ADMIN_IDS": [123456789],
    "SUPPORT_USERNAME": "test_support_bot",
    "DATABASE_URL": "postgresql+asyncpg://u:p@127.0.0.1:5432/db",
    "DB_ENCRYPTION_KEY": "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
    "REDIS_URL": "redis://:password2@127.0.0.1:6379/0",
    "REDIS_PASSWORD": "password2",
    "YOOKASSA_SHOP_ID": "123456",
    "YOOKASSA_SECRET_KEY": "test_secret",
    "YOOKASSA_RETURN_URL": "https://t.me/{bot_username}",
    "YOOKASSA_WEBHOOK_PORT": 8080,
    "DOMAIN": "vpn.example.test",
    "SSL_EMAIL": "owner@example.test",
}


class RuntimeConfigContractTests(unittest.TestCase):
    def build(self, data):
        with patch.dict(os.environ, {}, clear=True):
            return Settings(_env_file=None, **data)

    def test_complete_config_is_valid(self):
        settings = self.build(dict(BASE))
        self.assertEqual(settings.DOMAIN, "vpn.example.test")

    def test_missing_critical_settings_fail(self):
        for key in (
            "ADMIN_IDS",
            "REDIS_URL",
            "REDIS_PASSWORD",
            "YOOKASSA_SHOP_ID",
            "YOOKASSA_SECRET_KEY",
            "YOOKASSA_RETURN_URL",
            "YOOKASSA_WEBHOOK_PORT",
            "DOMAIN",
            "SSL_EMAIL",
        ):
            with self.subTest(key=key):
                data = dict(BASE)
                data.pop(key)
                with self.assertRaises(ValidationError):
                    self.build(data)

    def test_empty_payment_contract_fails(self):
        for key in (
            "YOOKASSA_SHOP_ID",
            "YOOKASSA_SECRET_KEY",
            "DOMAIN",
            "SSL_EMAIL",
        ):
            with self.subTest(key=key):
                data = dict(BASE)
                data[key] = ""
                with self.assertRaises(ValidationError):
                    self.build(data)

    def test_empty_admin_ids_fail(self):
        data = dict(BASE)
        data["ADMIN_IDS"] = []
        with self.assertRaises(ValidationError):
            self.build(data)

    def test_removed_global_settings_fail(self):
        for key, value in (
            ("AMNEZIA_API_URL", "http://127.0.0.1:4001"),
            ("AMNEZIA_API_KEY", "old-global-key"),
            ("WEBHOOK_URL", "https://vpn.example.test/webhook/yookassa"),
        ):
            with self.subTest(key=key):
                data = dict(BASE)
                data[key] = value
                with self.assertRaises(ValidationError):
                    self.build(data)

    def test_main_has_no_payment_disabled_startup_mode(self):
        source = (ROOT / "bot" / "main.py").read_text(encoding="utf-8")
        self.assertNotIn(
            "if settings.YOOKASSA_SHOP_ID and settings.YOOKASSA_SECRET_KEY",
            source,
        )
        self.assertIn("webhook_runner = await start_webhook_server", source)
        self.assertIn(
            'logger.critical("Fatal error in main: %s", e, exc_info=True)\n        raise',
            source,
        )

    def test_quoted_env_values_are_sanitized(self):
        data = dict(BASE)
        data["ADMIN_IDS"] = "'[123456789]'"
        data["YOOKASSA_RETURN_URL"] = "'https://t.me/{bot_username}'"
        data["REDIS_PASSWORD"] = "'Redis_!@#%_pass'"
        settings = self.build(data)
        self.assertEqual(settings.ADMIN_IDS, [123456789])
        self.assertEqual(settings.YOOKASSA_RETURN_URL, "https://t.me/{bot_username}")
        self.assertEqual(settings.REDIS_PASSWORD, "Redis_!@#%_pass")



if __name__ == "__main__":
    unittest.main()
