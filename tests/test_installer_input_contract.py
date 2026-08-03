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

    def test_valid_install_input_does_not_need_amnezia_values(self):
        result = self.run_function(
            "BOT_TOKEN='123456:TEST_TOKEN'; "
            "DB_PASSWORD='password1'; REDIS_PASSWORD='password2'; "
            "ADMIN_IDS='123'; SUPPORT_USERNAME='test_support_bot'; "
            "YOOKASSA_SHOP_ID=''; YOOKASSA_SECRET_KEY=''; "
            "DOMAIN=''; SSL_EMAIL=''; validate_initial_input"
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

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

    def test_existing_env_requires_support_username(self):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text(
                'BOT_TOKEN="123456:TEST_TOKEN"\n'
                'ADMIN_IDS="[123]"\n'
                'DATABASE_URL="postgresql+asyncpg://u:p@127.0.0.1:5432/db"\n'
                'REDIS_URL="redis://127.0.0.1:6379/0"\n'
                'DB_ENCRYPTION_KEY="MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="\n',
                encoding="utf-8",
            )
            env_file.chmod(0o600)
            result = self.run_function(
                f"BOT_USER=$(id -un); ENV_FILE={str(env_file)!r}; validate_env_file"
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SUPPORT_USERNAME", result.stderr)

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
