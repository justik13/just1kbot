import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = ROOT / "scripts" / "uninstall_entrypoint.sh"
UNINSTALL = ROOT / "scripts" / "uninstall_foundation.sh"
CORE = ROOT / "scripts" / "lib" / "uninstall_safe_core.sh"
ACTIONS = ROOT / "scripts" / "lib" / "uninstall_safe_actions.sh"
OWNERSHIP = ROOT / "scripts" / "lib" / "uninstall_safe_ownership.sh"


class UninstallVerificationContractTests(unittest.TestCase):
    def test_scripts_parse(self):
        for script in (ENTRYPOINT, UNINSTALL, CORE, ACTIONS, OWNERSHIP):
            result = subprocess.run(
                ["bash", "-n", str(script)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_official_entrypoint_only_execs_manifest_uninstall(self):
        source = ENTRYPOINT.read_text(encoding="utf-8")
        self.assertIn("uninstall_foundation.sh", source)
        self.assertIn('exec /bin/bash "$TARGET" "$@"', source)
        self.assertIn("script writable для group/other", source)
        for forbidden in ("rm ", "userdel", "dropdb", "redis-cli", "ufw "):
            self.assertNotIn(forbidden, source)

    def test_manifest_preflight_precedes_destructive_actions(self):
        source = UNINSTALL.read_text(encoding="utf-8")
        main = source[source.index("main()") :]
        self.assertLess(main.index("manifest_preflight"), main.index("stop_units"))
        self.assertLess(main.index("read_env"), main.index("stop_units"))
        self.assertLess(main.index("confirm"), main.index("stop_units"))
        self.assertLess(main.index("prepare_postgres"), main.index("stop_units"))
        self.assertLess(main.index("stop_units"), main.index("remove_nginx"))
        self.assertLess(main.index("remove_files"), main.index("post_verify"))

    def test_keep_data_creates_verified_backup_before_stop(self):
        entry = UNINSTALL.read_text(encoding="utf-8")
        core = CORE.read_text(encoding="utf-8")
        main = entry[entry.index("main()") :]
        self.assertIn("backup_before_keep", main)
        self.assertLess(main.index("backup_before_keep"), main.index("stop_units"))
        for marker in (
            "just1kbot-backup.service",
            "verify_backup.sh",
            "AGE_IDENTITY_FILE",
            "verified backup",
        ):
            self.assertIn(marker, core)

    def test_every_removed_path_requires_manifest_ownership(self):
        source = ACTIONS.read_text(encoding="utf-8")
        ownership = OWNERSHIP.read_text(encoding="utf-8")
        for marker in (
            "require_owned_path",
            "remove_owned_file",
            "remove_owned_tree",
            "foundation_manifest_has",
            "service user ownership отсутствует",
            "Nginx enabled link отсутствует в ownership manifest",
        ):
            self.assertIn(marker, source)
        self.assertIn(
            'remove_owned_file /etc/systemd/system/just1kbot.service systemd:just1kbot.service',
            source,
        )
        self.assertIn(
            'remove_owned_file "$REDIS_UNIT" "systemd:$REDIS_SERVICE"',
            source,
        )
        self.assertIn("owned_nginx_site", ownership)
        self.assertIn("owned_certificate", ownership)

    def test_uninstall_does_not_mutate_global_or_node_state(self):
        combined = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (UNINSTALL, CORE, ACTIONS, OWNERSHIP)
        )
        for forbidden in (
            "setup-amnezia-api.sh",
            "just1kbot-amnezia",
            "/etc/just1kbot-amnezia.conf",
            "ufw ",
            "iptables ",
            "nft ",
            "docker ",
            "sed -i /etc/redis/redis.conf",
            "rm -f /etc/redis/redis.conf",
            "rm -rf /etc/redis",
            "> /etc/redis/redis.conf",
            ">> /etc/redis/redis.conf",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, combined)
        self.assertIn("Firewall", UNINSTALL.read_text(encoding="utf-8"))

    def test_purge_postgresql_is_manifest_owner_and_marker_bounded(self):
        actions = ACTIONS.read_text(encoding="utf-8")
        ownership = OWNERSHIP.read_text(encoding="utf-8")
        self.assertIn("^just1kbot_(stg|rb|fail)_", actions)
        self.assertIn("DROP ROLE IF EXISTS", actions)
        self.assertNotIn("DROP OWNED", actions)
        self.assertNotIn("DROP DATABASE postgres", actions)
        for marker in (
            "postgres_manifest_state",
            "postgres_database_owner",
            "database_owner",
            "database ownership COMMENT",
            "role ownership COMMENT",
            "installation-id=%s",
        ):
            self.assertIn(marker, ownership)
        self.assertIn(
            "COMMENT markers отсутствуют; ownership подтверждён manifest и database owner",
            ownership,
        )
        self.assertIn(
            "database ownership COMMENT не подтверждает manifest installation ID",
            ownership,
        )

    def test_post_verify_reports_all_managed_leftovers(self):
        actions = ACTIONS.read_text(encoding="utf-8")
        ownership = OWNERSHIP.read_text(encoding="utf-8")
        base = actions[actions.index("post_verify()") :]
        for marker in (
            "$PROJECT_DIR",
            "$REDIS_CONFIG",
            "$REDIS_DATA_DIR",
            "$REDIS_UNIT",
            "$CLI_PATH",
            "user:$BOT_USER",
            "active:$unit",
            "uninstall оставил resources",
        ):
            self.assertIn(marker, base)
        for marker in (
            "nginx-site:$DOMAIN",
            "nginx-enabled:$DOMAIN",
            "certbot:$DOMAIN",
            "verify_postgres_absent",
            "ownership-aware uninstall verification found leftovers",
        ):
            self.assertIn(marker, ownership)


if __name__ == "__main__":
    unittest.main()
