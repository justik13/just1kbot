import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESET = ROOT / "scripts" / "reset_legacy_install.sh"


class LegacyResetContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = RESET.read_text(encoding="utf-8")

    def test_script_parses(self):
        result = subprocess.run(
            ["bash", "-n", str(RESET)],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_reset_is_explicitly_confirmed(self):
        self.assertIn("RESET_PHRASE='RESET JUST1KBOT'", self.source)
        self.assertIn("require_no_manifest", self.source)
        self.assertIn("confirm_reset", self.source)
        self.assertIn("[[ \"$answer\" == \"$RESET_PHRASE\" ]]", self.source)

    def test_legacy_cli_requires_strong_markers_before_deletion(self):
        self.assertIn("root_owned_regular_file \"$CLI_PATH\"", self.source)
        self.assertIn("grep -Fq 'Just1kBot' \"$CLI_PATH\"", self.source)
        self.assertIn("grep -Fq '/opt/just1kbot' \"$CLI_PATH\"", self.source)
        self.assertIn("legacy_cli_looks_managed || die", self.source)

    def test_units_are_verified_before_stop_or_disable(self):
        function_source = re.search(
            r"stop_and_remove_unit\(\) \{(?P<body>.*?)\n\}",
            self.source,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(function_source)
        body = function_source.group("body")
        ownership_index = body.index("managed_unit_file \"$path\" ||")
        stop_index = body.index('systemctl stop "$unit"')
        disable_index = body.index('systemctl disable "$unit"')
        self.assertLess(ownership_index, stop_index)
        self.assertLess(ownership_index, disable_index)
        self.assertIn("warn \"Чужой или неподтверждённый unit оставлен и не остановлен: $path\"", body)

    def test_redis_data_requires_managed_unit_and_config(self):
        self.assertIn("managed_redis_config()", self.source)
        self.assertIn("legacy_redis_is_managed()", self.source)
        self.assertIn("managed_unit_file \"$REDIS_UNIT\"", self.source)
        self.assertIn("managed_redis_config || return 1", self.source)
        self.assertIn("detect_legacy_redis_ownership", self.source)
        self.assertIn("REDIS_RUNTIME_OWNED=1", self.source)
        self.assertIn("REDIS_RUNTIME_OWNED == 1", self.source)
        self.assertIn("ownership не подтверждён через managed unit + config", self.source)

        runtime_source = re.search(
            r"remove_legacy_runtime\(\) \{(?P<body>.*?)\n\}",
            self.source,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(runtime_source)
        body = runtime_source.group("body")
        ownership_gate = body.index("if (( REDIS_RUNTIME_OWNED == 1 )); then")
        data_delete = body.index('rm -rf --one-file-system -- "$REDIS_DATA_DIR"')
        config_delete = body.index('rm -f -- "$REDIS_CONFIG"')
        self.assertLess(ownership_gate, data_delete)
        self.assertLess(ownership_gate, config_delete)

    def test_service_user_requires_journal_creation_proof(self):
        self.assertIn("journal_created_resource()", self.source)
        self.assertIn('journal_created_resource "service-user:$BOT_USER"', self.source)

        function_source = re.search(
            r"remove_service_user\(\) \{(?P<body>.*?)\n\}",
            self.source,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(function_source)
        body = function_source.group("body")
        proof_index = body.index('journal_created_resource "service-user:$BOT_USER"')
        delete_index = body.index('userdel "$BOT_USER"')
        self.assertLess(proof_index, delete_index)
        self.assertIn("transaction journal не подтверждает, что его создал installer", body)

    def test_postgres_is_deleted_only_with_matching_ownership_markers(self):
        self.assertIn("managed-by=just1kbot\\;installation-id=*", self.source)
        self.assertIn("[[ \"$db_comment\" == \"$role_comment\" ]]", self.source)
        self.assertIn("runuser -u postgres -- dropdb", self.source)
        self.assertIn("DROP ROLE IF EXISTS just1kbot;", self.source)

    def test_global_redis_and_firewall_are_not_touched(self):
        self.assertNotIn("sed -i", self.source)
        self.assertNotIn("ufw", self.source)
        self.assertNotIn("iptables", self.source)
        self.assertNotIn("nft", self.source)
        self.assertIn("Global Redis конфигурация /etc/redis/redis.conf и firewall намеренно не изменялись.", self.source)
        self.assertIn("REDIS_CONFIG=${REDIS_CONFIG:-/etc/just1kbot/redis.conf}", self.source)

    def test_only_known_units_and_helpers_are_targeted(self):
        for unit in (
            "just1kbot.service",
            "just1kbot-redis.service",
            "just1kbot-healthcheck.service",
            "just1kbot-healthcheck.timer",
            "just1kbot-backup.service",
            "just1kbot-backup.timer",
        ):
            self.assertIn(unit, self.source)
        self.assertIn("remove_known_helper", self.source)
        self.assertIn("Just1kBot", self.source)


if __name__ == "__main__":
    unittest.main()
