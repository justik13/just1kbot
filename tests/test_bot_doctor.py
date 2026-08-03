import pathlib
import subprocess
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy.sh"
CONTROL_BASE = ROOT / "scripts" / "lib" / "control_plane.sh"
CONTROL_COMPLETION = ROOT / "scripts" / "lib" / "control_plane_completion.sh"
DOCTOR = ROOT / "scripts" / "ops" / "doctor.sh"
DOCTOR_COMPLETE = ROOT / "scripts" / "ops" / "doctor_complete.sh"
DOCTOR_JSON = ROOT / "scripts" / "ops" / "doctor_json.sh"
SUPPORT_BUNDLE = ROOT / "scripts" / "ops" / "support_bundle.sh"


class BotDoctorContractTests(unittest.TestCase):
    def test_doctor_scripts_parse(self):
        for script in (DOCTOR, DOCTOR_COMPLETE, DOCTOR_JSON, SUPPORT_BUNDLE):
            with self.subTest(script=script):
                subprocess.run(["bash", "-n", str(script)], check=True)

    def test_control_plane_exposes_complete_doctor_and_post_operation_gate(self):
        loader = DEPLOY.read_text(encoding="utf-8")
        base = CONTROL_BASE.read_text(encoding="utf-8")
        completion = CONTROL_COMPLETION.read_text(encoding="utf-8")
        self.assertIn('source "$completion"', loader)
        self.assertIn("sudo bash deploy.sh doctor", base)
        self.assertIn("doctor --json", completion)
        self.assertIn("call_script ops/doctor_complete.sh", completion)
        self.assertIn("call_script ops/doctor_json.sh", completion)
        self.assertIn("smoke()", completion)
        self.assertIn("ops/doctor_complete.sh --smoke", completion)
        self.assertIn("--check", base)
        self.assertIn("--dry-run", base)
        self.assertIn("поздний smoke сам не выполняет rollback", completion)

    def test_doctor_checks_bot_runtime_contract(self):
        text = DOCTOR.read_text(encoding="utf-8")
        complete = DOCTOR_COMPLETE.read_text(encoding="utf-8")
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
        for marker in (
            "PostgreSQL database ownership comment",
            "PostgreSQL role ownership comment",
            "Global CLI ownership proof",
            "External proxy contract",
            "proxy_mode",
        ):
            self.assertIn(marker, complete)

    def test_doctor_is_read_only_and_support_bundle_redacts_failures(self):
        combined = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (DOCTOR, DOCTOR_COMPLETE, DOCTOR_JSON)
        )
        for forbidden in (
            "alembic upgrade",
            "systemctl restart",
            "systemctl start",
            "systemctl enable",
            "DROP ",
            "DELETE FROM",
            'cat "$ENV_FILE"',
        ):
            self.assertNotIn(forbidden, combined)

        support = SUPPORT_BUNDLE.read_text(encoding="utf-8")
        for marker in (
            "<redacted-telegram-token>",
            "<redacted>@",
            "AGE-SECRET-KEY",
            "url_credentials",
            "secret_key",
            "assignment",
            "BOT_TOKEN",
            "DATABASE_URL",
            "REDIS_URL",
        ):
            self.assertIn(marker, support)
        self.assertNotIn('cp -- /opt/just1kbot/.env', support)
        self.assertNotIn("pg_dump", support)


if __name__ == "__main__":
    unittest.main()
