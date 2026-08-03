import json
import os
import pathlib
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
FOUNDATION = (SCRIPTS / "lib" / "installer_foundation.sh").read_text(
    encoding="utf-8"
)
PLATFORM = (SCRIPTS / "lib" / "install_safe_platform.sh").read_text(
    encoding="utf-8"
)
RUNTIME = (SCRIPTS / "lib" / "install_safe_runtime.sh").read_text(
    encoding="utf-8"
)
DISPATCH = (SCRIPTS / "lib" / "install_safe_dispatch.sh").read_text(
    encoding="utf-8"
)
UNINSTALL = "\n".join(
    [
        (SCRIPTS / "uninstall_foundation.sh").read_text(encoding="utf-8"),
        (SCRIPTS / "lib" / "uninstall_safe_core.sh").read_text(
            encoding="utf-8"
        ),
        (SCRIPTS / "lib" / "uninstall_safe_actions.sh").read_text(
            encoding="utf-8"
        ),
    ]
)
INSPECTOR = SCRIPTS / "inspect_install_state.sh"


class SharedHostInstallerContractTests(unittest.TestCase):
    def test_global_firewall_and_redis_are_never_mutated(self):
        safe_installer = "\n".join((FOUNDATION, PLATFORM, RUNTIME, DISPATCH))
        for forbidden in (
            "/etc/redis/redis.conf",
            "ufw --force enable",
            "ufw default",
            "ufw allow",
            "ufw deny",
            "iptables ",
            "nft ",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, safe_installer)
                self.assertNotIn(forbidden, UNINSTALL)
        self.assertIn("foundation_firewall_noop", FOUNDATION)
        self.assertIn("Firewall не изменяется", FOUNDATION)
        self.assertIn("firewall_managed", FOUNDATION)

    def test_redis_is_a_dedicated_local_service(self):
        for marker in (
            "REDIS_SERVICE:=just1kbot-redis.service",
            "REDIS_PORT:=6380",
            "REDIS_CONFIG:=/etc/just1kbot/redis.conf",
            "REDIS_DATA_DIR:=$STATE_ROOT/redis",
            "bind 127.0.0.1 ::1",
            "port ${REDIS_PORT}",
            "maxmemory-policy noeviction",
            'rename-command FLUSHALL ""',
            'rename-command FLUSHDB ""',
            "ProtectSystem=strict",
            "ReadWritePaths=${REDIS_DATA_DIR} /run/just1kbot-redis",
        ):
            self.assertIn(marker, FOUNDATION)
        self.assertIn("Requires=$PG_UNIT $REDIS_SERVICE", RUNTIME)
        self.assertIn("Requires=just1kbot-redis.service", RUNTIME)

    def test_preflight_happens_before_package_install(self):
        initial = DISPATCH[
            DISPATCH.index('if [[ "$INITIAL_INSTALL" == true ]]') :
        ]
        self.assertLess(
            initial.index("preflight_before_packages"),
            initial.index("install_dependencies"),
        )
        preflight = DISPATCH[
            DISPATCH.index("preflight_before_packages()") : DISPATCH.index(
                "run_management_action()"
            )
        ]
        for marker in (
            "foundation_preflight_static_resources",
            "foundation_preflight_domain",
            "preflight_postgres_names_absent",
        ):
            self.assertIn(marker, preflight)

    def test_nginx_default_site_is_out_of_scope(self):
        self.assertNotIn("sites-enabled/default", RUNTIME)
        self.assertNotIn("sites-enabled/default", FOUNDATION)
        self.assertNotIn("sites-enabled/default", UNINSTALL)
        self.assertIn("# Managed by Just1kBot ownership manifest", FOUNDATION)
        self.assertIn("nginx -t", FOUNDATION)
        self.assertIn("Предыдущее состояние восстановлено", FOUNDATION)

    def test_manifest_and_journal_are_durable_and_fail_closed(self):
        for marker in (
            "manifest.json",
            "transaction.json",
            "schema_version",
            "installation_id",
            "managed_resources",
            "foundation_journal_begin",
            "foundation_journal_update_phase",
            "foundation_journal_created_resources",
            "UNFINISHED_TRANSACTION",
        ):
            self.assertIn(marker, FOUNDATION)
        self.assertIn("install-recover", DISPATCH)
        self.assertIn("install-rollback", DISPATCH)

    def test_locked_dependencies_and_exact_platform_are_mandatory(self):
        for marker in (
            "requirements.lock",
            "--require-hashes",
            "--no-deps",
            "Ubuntu 24.04",
            "version_info[:2]==(3,12)",
            "/usr/sbin/nologin",
        ):
            self.assertIn(marker, PLATFORM)

    def test_uninstall_is_manifest_bounded(self):
        for marker in (
            "foundation_manifest_require",
            "require_owned_path",
            "remove_owned_file",
            "remove_owned_tree",
            "service user ownership отсутствует",
            "post_verify",
        ):
            self.assertIn(marker, UNINSTALL)
        self.assertNotIn("rm -rf -- /etc", UNINSTALL)
        self.assertNotIn("userdel -f", UNINSTALL)

    def _inspector_env(self, root: pathlib.Path) -> dict[str, str]:
        env = os.environ.copy()
        env.update(
            {
                "PROJECT_DIR": str(root / "opt" / "just1kbot"),
                "UNIT_FILE": str(root / "etc" / "just1kbot.service"),
                "BOT_HOME": str(root / "home" / "just1kbot"),
                "CLI_SBIN": str(root / "sbin" / "just1kbot"),
                "CLI_BIN": str(root / "bin" / "just1kbot"),
                "STATE_ROOT": str(root / "state"),
                "REDIS_CONFIG": str(root / "etc" / "redis.conf"),
                "REDIS_DATA_DIR": str(root / "state" / "redis"),
                "REDIS_UNIT": str(root / "etc" / "just1kbot-redis.service"),
                "BACKUP_DIR": str(root / "backups"),
                "BACKUP_CONF": str(root / "etc" / "backup.conf"),
                "BACKUP_IDENTITY": str(root / "etc" / "backup.agekey"),
                "BACKUP_TOOL": str(root / "tools" / "backup"),
                "RESTORE_TOOL": str(root / "tools" / "restore"),
                "HEALTHCHECK_TOOL": str(root / "tools" / "health"),
                "VERIFY_TOOL": str(root / "tools" / "verify"),
                "REHEARSAL_TOOL": str(root / "tools" / "rehearsal"),
                "INSTALL_STATE_SKIP_USER_LOOKUP": "1",
            }
        )
        return env

    def test_state_inspector_reports_clean_host_at_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                ["bash", str(INSPECTOR), "--json", "--require-safe"],
                env=self._inspector_env(pathlib.Path(directory)),
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["state"], "clean")

    def test_state_inspector_blocks_symlink_collision_at_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            project = root / "opt" / "just1kbot"
            project.parent.mkdir(parents=True)
            project.symlink_to(root / "foreign")
            result = subprocess.run(
                ["bash", str(INSPECTOR), "--json", "--require-safe"],
                env=self._inspector_env(root),
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertEqual(result.returncode, 20, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["state"], "foreign_collision")
        self.assertIn("symlink", payload["reason"])

    def test_partial_install_is_not_safe_for_deploy(self):
        source = INSPECTOR.read_text(encoding="utf-8")
        deploy_cases = source[source.index("state_exit_code()") :]
        self.assertNotIn("deploy:partial_install", deploy_cases)
        self.assertIn("*:partial_install) return 23", deploy_cases)
        self.assertIn("install-recover или install-rollback", source)


if __name__ == "__main__":
    unittest.main()
