import os
import pathlib
import types
import unittest
from unittest.mock import patch

from config import settings


ROOT = pathlib.Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "scripts" / "deploy.sh"


class ServiceHomeHotfixTests(unittest.TestCase):
    def test_hardened_unit_keeps_protect_home_enabled(self):
        deploy = DEPLOY.read_text(encoding="utf-8")
        self.assertIn("ProtectHome=true", deploy)

    def test_service_account_uses_runtime_home(self):
        with (
            patch.object(settings.os, "geteuid", return_value=12345),
            patch.object(
                settings.pwd,
                "getpwuid",
                return_value=types.SimpleNamespace(pw_name="just1kbot"),
            ),
            patch.dict(os.environ, {"HOME": "/home/just1kbot"}, clear=False),
        ):
            settings._configure_database_client_home()
            self.assertEqual(os.environ["HOME"], "/run/just1kbot")

    def test_other_accounts_keep_their_home(self):
        with (
            patch.object(settings.os, "geteuid", return_value=12345),
            patch.object(
                settings.pwd,
                "getpwuid",
                return_value=types.SimpleNamespace(pw_name="runner"),
            ),
            patch.dict(os.environ, {"HOME": "/home/runner"}, clear=False),
        ):
            settings._configure_database_client_home()
            self.assertEqual(os.environ["HOME"], "/home/runner")

    def test_missing_passwd_entry_is_fail_safe(self):
        with (
            patch.object(settings.os, "geteuid", return_value=12345),
            patch.object(settings.pwd, "getpwuid", side_effect=KeyError),
            patch.dict(os.environ, {"HOME": "/tmp/original"}, clear=False),
        ):
            settings._configure_database_client_home()
            self.assertEqual(os.environ["HOME"], "/tmp/original")


if __name__ == "__main__":
    unittest.main()
