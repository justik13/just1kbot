import pathlib
import subprocess
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy.sh"
CONTROL = ROOT / "scripts" / "lib" / "control_plane.sh"
DOCTOR = ROOT / "scripts" / "ops" / "doctor.sh"


class BotDoctorContractTests(unittest.TestCase):
    def test_doctor_script_parses(self):
        subprocess.run(["bash", "-n", str(DOCTOR)], check=True)

    def test_control_plane_exposes_doctor_and_post_operation_gate(self):
        loader = DEPLOY.read_text(encoding="utf-8")
        text = CONTROL.read_text(encoding="utf-8")
        self.assertIn('source "$module"', loader)
        self.assertIn("sudo bash deploy.sh doctor", text)
        self.assertIn("call_script ops/doctor.sh", text)
        self.assertIn("smoke()", text)
        self.assertIn("call_script ops/doctor.sh --smoke", text)
        self.assertIn("--check", text)
        self.assertIn("--dry-run", text)
        self.assertIn("автоматический rollback на этом этапе не выполнялся", text)

    def test_doctor_checks_bot_runtime_contract(self):
        text = DOCTOR.read_text(encoding="utf-8")
        for marker in (
            "flock -s -w 5",
            "ProtectHome=true",
            "JUST1KBOT_HEARTBEAT_FILE=/run/just1kbot/heartbeat",
            "root:just1kbot 750",
            "root:just1kbot 640",
            "MAX_HEARTBEAT_AGE=180",
            ".release-version",
            "just1kbot-healthcheck.timer",
            "just1kbot-backup.timer",
            "just1kbot-pg-v1-????????T??????Z.tar.age",
            "sha256sum",
            "ScriptDirectory.from_config(config).get_heads()",
            "SELECT version_num FROM alembic_version",
            "redis_client.ping()",
            "bot.get_me()",
            "HOME=/run/just1kbot",
        ):
            self.assertIn(marker, text)

    def test_doctor_is_read_only_and_redacts_failures(self):
        text = DOCTOR.read_text(encoding="utf-8")
        self.assertNotIn("alembic upgrade", text)
        self.assertNotIn("systemctl restart", text)
        self.assertNotIn("systemctl start", text)
        self.assertNotIn("systemctl enable", text)
        self.assertNotIn("DROP ", text)
        self.assertNotIn("DELETE FROM", text)
        self.assertNotIn('cat "$ENV_FILE"', text)
        self.assertIn("TELEGRAM_TOKEN_REDACTED", text)
        self.assertIn("postgresql(\\+asyncpg)?://", text)
        self.assertIn("redis://", text)


if __name__ == "__main__":
    unittest.main()
