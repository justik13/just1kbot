import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class GreenfieldContractTests(unittest.TestCase):
    def read(self, relative: str) -> str:
        return (ROOT / relative).read_text(encoding="utf-8")

    def test_single_database_baseline_until_production(self):
        versions = sorted(
            path.name
            for path in (ROOT / "alembic" / "versions").glob("*.py")
            if path.name != "__init__.py"
        )
        self.assertEqual(versions, ["0001_clean_baseline.py"])

        baseline = self.read("alembic/versions/0001_clean_baseline.py")
        self.assertIn('revision = "0001_clean_baseline"', baseline)
        self.assertIn("down_revision = None", baseline)

    def test_yookassa_cannot_be_disabled_after_settings_load(self):
        service = self.read("services/yookassa_service.py")
        balance = self.read("bot/handlers/payment/balance_routes.py")

        self.assertNotIn("CONFIGURATION =", service)
        self.assertNotIn('getattr(s, "YOOKASSA_SHOP_ID"', service)
        self.assertNotIn('getattr(s, "YOOKASSA_SECRET_KEY"', service)
        self.assertIn("settings.YOOKASSA_SHOP_ID", service)
        self.assertIn("settings.YOOKASSA_SECRET_KEY", service)
        self.assertNotIn(
            "if not settings.YOOKASSA_SHOP_ID",
            balance,
        )

    def test_runtime_failures_exit_nonzero(self):
        source = self.read("bot/main.py")

        self.assertNotIn("DB_ENCRYPTION_KEY пуст", source)
        self.assertIn(
            "DB_ENCRYPTION_KEY is not a valid Fernet key",
            source,
        )
        self.assertIn("raise polling_error", source)
        self.assertIn(
            "Telegram polling stopped unexpectedly",
            source,
        )

    def test_deploy_has_no_nonexistent_install_compatibility(self):
        source = self.read("scripts/deploy.sh")

        self.assertNotIn(
            '"$owner" == "$BOT_USER" && "$group" == "$BOT_USER"',
            source,
        )
        self.assertNotIn("rollback_heartbeat=obsolete", source)
        self.assertNotIn(
            'HEARTBEAT_FILE="$PROJECT_DIR/.heartbeat"',
            source,
        )


if __name__ == "__main__":
    unittest.main()
