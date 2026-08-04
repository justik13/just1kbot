import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESET = ROOT / "scripts" / "reset_legacy_install.sh"


class LegacyResetContractTests(unittest.TestCase):
    def test_script_parses(self):
        result = subprocess.run(
            ["bash", "-n", str(RESET)],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_reset_is_explicitly_confirmed(self):
        source = RESET.read_text(encoding="utf-8")
        self.assertIn("RESET_PHRASE='RESET JUST1KBOT'", source)
        self.assertIn("require_no_manifest", source)
        self.assertIn("confirm_reset", source)
        self.assertIn("[[ \"$answer\" == \"$RESET_PHRASE\" ]]", source)

    def test_legacy_cli_requires_strong_markers_before_deletion(self):
        source = RESET.read_text(encoding="utf-8")
        self.assertIn("root_owned_regular_file \"$CLI_PATH\"", source)
        self.assertIn("grep -Fq 'Just1kBot' \"$CLI_PATH\"", source)
        self.assertIn("grep -Fq '/opt/just1kbot' \"$CLI_PATH\"", source)
        self.assertIn("legacy_cli_looks_managed || die", source)

    def test_postgres_is_deleted_only_with_matching_ownership_markers(self):
        source = RESET.read_text(encoding="utf-8")
        self.assertIn("managed-by=just1kbot\\;installation-id=*", source)
        self.assertIn("[[ \"$db_comment\" == \"$role_comment\" ]]", source)
        self.assertIn("runuser -u postgres -- dropdb", source)
        self.assertIn("DROP ROLE IF EXISTS", source)

    def test_global_redis_and_firewall_are_not_touched(self):
        source = RESET.read_text(encoding="utf-8")
        self.assertNotIn("/etc/redis/redis.conf", source)
        self.assertNotIn("ufw", source)
        self.assertNotIn("iptables", source)
        self.assertNotIn("nft", source)
        self.assertIn("Global Redis (/etc/redis/redis.conf) и firewall намеренно не изменялись.", source)

    def test_only_known_units_and_helpers_are_targeted(self):
        source = RESET.read_text(encoding="utf-8")
        for unit in (
            "just1kbot.service",
            "just1kbot-redis.service",
            "just1kbot-healthcheck.service",
            "just1kbot-healthcheck.timer",
            "just1kbot-backup.service",
            "just1kbot-backup.timer",
        ):
            self.assertIn(unit, source)
        self.assertIn("remove_known_helper", source)
        self.assertIn("Just1kBot", source)


if __name__ == "__main__":
    unittest.main()
