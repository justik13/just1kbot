import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "scripts" / "deploy.sh"
UNINSTALL = ROOT / "scripts" / "uninstall.sh"


class ServiceHomeHotfixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.deploy = DEPLOY.read_text(encoding="utf-8")
        cls.uninstall = UNINSTALL.read_text(encoding="utf-8")

    def test_protected_home_uses_runtime_home_for_database_clients(self):
        for marker in (
            'SERVICE_HOME="$RUNTIME_DIR"',
            'Environment=HOME=${SERVICE_HOME}',
            'env HOME="$SERVICE_HOME" PYTHONPATH="$PROJECT_DIR"',
            'env HOME=/run/just1kbot PYTHONPATH="$PROJECT_DIR"',
            "ProtectHome=true",
        ):
            self.assertIn(marker, self.deploy)

    def test_account_home_is_validated_and_postgresql_directory_is_accessible(self):
        for marker in (
            'BOT_ACCOUNT_HOME="/home/$BOT_USER"',
            "validate_bot_account_home",
            'useradd -r -M -d "$BOT_ACCOUNT_HOME"',
            'useradd -r -m -d "$BOT_ACCOUNT_HOME"',
            'install -d -o "$BOT_USER" -g "$BOT_USER" -m 0700 "$BOT_ACCOUNT_HOME/.postgresql"',
        ):
            self.assertIn(marker, self.deploy)

    def test_purge_removes_and_verifies_exact_service_home(self):
        for marker in (
            "BOT_HOME=/home/just1kbot",
            "safe_remove_bot_home",
            "purge_bot_user",
            '[[ "$configured_home" == "$BOT_HOME" ]]',
            'rm -rf --one-file-system -- "$BOT_HOME"',
            "service home still exists after purge",
        ):
            self.assertIn(marker, self.uninstall)

        purge_saved = self.uninstall[self.uninstall.index("purge_saved(){") :]
        self.assertIn("purge_bot_user", purge_saved)
        self.assertNotIn('userdel "$BOT_USER"\n}', purge_saved)


if __name__ == "__main__":
    unittest.main()
