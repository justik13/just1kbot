import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ROOT_DEPLOY = ROOT / "deploy.sh"
CORE = ROOT / "scripts" / "lib" / "deploy_core.inc"
ADAPTER = ROOT / "scripts" / "deploy.sh"


class InstallerInputContractTests(unittest.TestCase):
    def run_function(self, script: str):
        env = os.environ.copy()
        env.update({"DEPLOY_FUNCTIONS_ONLY": "1", "DEPLOY_TEST_MODE": "1"})
        command = textwrap.dedent(
            f"""
            set -Eeuo pipefail
            source {str(ROOT_DEPLOY)!r}
            LOG_FILE=/tmp/just1kbot-installer-contract.log
            {script}
            """
        )
        return subprocess.run(
            ["bash", "-c", command],
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_initial_install_has_no_global_amnezia_credentials(self):
        source = CORE.read_text(encoding="utf-8")
        adapter = ADAPTER.read_text(encoding="utf-8")
        self.assertNotIn("Amnezia API URL", source)
        self.assertNotIn("AMNEZIA_API_URL=${", source)
        self.assertNotIn("AMNEZIA_API_KEY=${", source)
        self.assertNotIn("write_env_var AMNEZIA_API_URL", source + adapter)
        self.assertNotIn("write_env_var AMNEZIA_API_KEY", source + adapter)
        self.assertNotIn("write_env_var WEBHOOK_URL", source + adapter)

    def test_valid_install_input_requires_payment_contract_but_not_amnezia(self):
        result = self.run_function(
            "BOT_TOKEN='123456:TEST_TOKEN'; "
            "DB_PASSWORD='password1'; REDIS_PASSWORD='password2'; "
            "ADMIN_IDS='123'; SUPPORT_USERNAME='test_support_bot'; "
            "YOOKASSA_SHOP_ID='123456'; YOOKASSA_SECRET_KEY='secret'; "
            "YOOKASSA_RETURN_URL='https://t.me/{bot_username}'; "
            "YOOKASSA_WEBHOOK_PORT='8080'; "
            "DOMAIN='vpn.example.test'; SSL_EMAIL='owner@example.test'; "
            "validate_initial_input"
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        source = CORE.read_text(encoding="utf-8")
        self.assertNotIn("Enter — отключено", source)
        self.assertNotIn("YooKassa отключена", source)

        for missing_assignment, expected in (
            ("YOOKASSA_SHOP_ID='';", "YOOKASSA"),
            ("YOOKASSA_SECRET_KEY='';", "YOOKASSA"),
            ("DOMAIN='';", "DOMAIN"),
            ("SSL_EMAIL='';", "SSL_EMAIL"),
        ):
            with self.subTest(missing_assignment=missing_assignment):
                command = (
                    "BOT_TOKEN='123456:TEST_TOKEN'; "
                    "DB_PASSWORD='password1'; REDIS_PASSWORD='password2'; "
                    "ADMIN_IDS='123'; SUPPORT_USERNAME='test_support_bot'; "
                    "YOOKASSA_SHOP_ID='123456'; YOOKASSA_SECRET_KEY='secret'; "
                    "YOOKASSA_RETURN_URL='https://t.me/{bot_username}'; "
                    "YOOKASSA_WEBHOOK_PORT='8080'; "
                    "DOMAIN='vpn.example.test'; SSL_EMAIL='owner@example.test'; "
                    + missing_assignment
                    + " validate_initial_input"
                )
                missing_result = self.run_function(command)
                self.assertNotEqual(missing_result.returncode, 0)
                self.assertIn(expected, missing_result.stderr)

    def test_domain_rejects_placeholder_certbot_email(self):
        result = self.run_function(
            "BOT_TOKEN='123456:TEST_TOKEN'; "
            "DB_PASSWORD='password1'; REDIS_PASSWORD='password2'; "
            "ADMIN_IDS='123'; SUPPORT_USERNAME='test_support_bot'; "
            "YOOKASSA_SHOP_ID='shop'; YOOKASSA_SECRET_KEY='secret'; "
            "DOMAIN='vpn.example.com'; SSL_EMAIL='admin@example.com'; "
            "validate_initial_input"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SSL_EMAIL", result.stderr)

    def write_complete_env(self, env_file: Path, extra: str = "") -> None:
        env_file.write_text(
            'BOT_TOKEN="123456:TEST_TOKEN"\n'
            'ADMIN_IDS="[123]"\n'
            'SUPPORT_USERNAME="test_support_bot"\n'
            'DATABASE_URL="postgresql+asyncpg://u:p@127.0.0.1:5432/db"\n'
            'REDIS_URL="redis://:password2@127.0.0.1:6379/0"\n'
            'DB_ENCRYPTION_KEY="MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="\n'
            'REDIS_PASSWORD="password2"\n'
            'YOOKASSA_SHOP_ID="123456"\n'
            'YOOKASSA_SECRET_KEY="secret"\n'
            'YOOKASSA_RETURN_URL="https://t.me/test_bot"\n'
            'YOOKASSA_WEBHOOK_PORT="8080"\n'
            'DOMAIN="vpn.example.test"\n'
            'SSL_EMAIL="owner@example.test"\n'
            + extra,
            encoding="utf-8",
        )
        env_file.chmod(0o600)

    def test_existing_env_requires_support_username(self):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            self.write_complete_env(env_file)
            text = env_file.read_text(encoding="utf-8")
            env_file.write_text(
                text.replace('SUPPORT_USERNAME="test_support_bot"\n', ""),
                encoding="utf-8",
            )
            result = self.run_function(
                f"BOT_USER=$(id -un); ENV_FILE={str(env_file)!r}; validate_env_file"
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SUPPORT_USERNAME", result.stderr)

    def test_existing_env_rejects_removed_settings(self):
        for key, value in (
            ("AMNEZIA_API_URL", "http://127.0.0.1:4001"),
            ("AMNEZIA_API_KEY", "old-global-key"),
            ("WEBHOOK_URL", "https://vpn.example.test/webhook/yookassa"),
        ):
            with self.subTest(key=key), tempfile.TemporaryDirectory() as directory:
                env_file = Path(directory) / ".env"
                self.write_complete_env(env_file, f'{key}="{value}"\n')
                result = self.run_function(
                    f"BOT_USER=$(id -un); ENV_FILE={str(env_file)!r}; validate_env_file"
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(key, result.stderr)

    def test_supported_os_boundaries(self):
        for os_id, version in (("ubuntu", "24.04"), ("debian", "12")):
            with self.subTest(os_id=os_id, version=version):
                result = self.run_function(
                    f"validate_supported_os {os_id!r} {version!r}"
                )
                self.assertEqual(result.returncode, 0, result.stderr)
        for os_id, version in (("ubuntu", "22.04"), ("debian", "11")):
            with self.subTest(os_id=os_id, version=version):
                result = self.run_function(
                    f"validate_supported_os {os_id!r} {version!r}"
                )
                self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
