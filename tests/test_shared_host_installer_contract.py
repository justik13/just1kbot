import json
import os
import pathlib
import subprocess
import tempfile
import textwrap
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
FOUNDATION = (SCRIPTS / "lib" / "installer_foundation.sh").read_text(
    encoding="utf-8"
)
PLATFORM = (SCRIPTS / "lib" / "install_safe_platform.sh").read_text(
    encoding="utf-8"
)
LOCK_POLICY_PATH = SCRIPTS / "lib" / "install_safe_lock_policy.sh"
LOCK_POLICY = LOCK_POLICY_PATH.read_text(encoding="utf-8")
RUNTIME = (SCRIPTS / "lib" / "install_safe_runtime.sh").read_text(
    encoding="utf-8"
)
ACTIVATION = (SCRIPTS / "lib" / "install_safe_activation_policy.sh").read_text(
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
        (SCRIPTS / "lib" / "uninstall_safe_ownership.sh").read_text(
            encoding="utf-8"
        ),
    ]
)
INSPECTOR = SCRIPTS / "inspect_install_state.sh"


class SharedHostInstallerContractTests(unittest.TestCase):
    def test_global_firewall_and_redis_are_never_mutated(self):
        safe_installer = "\n".join(
            (FOUNDATION, PLATFORM, RUNTIME, ACTIVATION, DISPATCH)
        )
        for forbidden in (
            "ufw --force enable",
            "ufw default",
            "ufw allow",
            "ufw deny",
            "iptables ",
            "nft ",
            "sed -i /etc/redis/redis.conf",
            "rm -f /etc/redis/redis.conf",
            "rm -rf /etc/redis",
            "> /etc/redis/redis.conf",
            ">> /etc/redis/redis.conf",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, safe_installer)
                self.assertNotIn(forbidden, UNINSTALL)
        self.assertIn("foundation_firewall_noop", FOUNDATION)
        self.assertIn("Firewall не изменяется", FOUNDATION)
        self.assertIn("firewall_managed", FOUNDATION)
        self.assertIn("REDIS_CONFIG:=/etc/just1kbot/redis.conf", FOUNDATION)

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
        self.assertIn("foundation_setup_dedicated_redis", ACTIVATION)
        self.assertNotIn("foundation_setup_dedicated_redis", DISPATCH)

    def test_preflight_happens_before_package_install(self):
        run_deploy = DISPATCH[DISPATCH.index("run_deploy()") :]
        self.assertLess(
            run_deploy.index("run_initial_read_only_preflight"),
            run_deploy.index("perform_deploy_mutations"),
        )
        mutations = DISPATCH[
            DISPATCH.index("perform_deploy_mutations()") : DISPATCH.index(
                "rollback_empty_pre_manifest_journal()"
            )
        ]
        self.assertLess(
            mutations.index("begin_installer_transaction"),
            mutations.index("install_dependencies"),
        )
        self.assertLess(
            mutations.index("install_dependencies"),
            mutations.index("ensure_manifest"),
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

    def test_durable_journal_is_first_mutation_before_apt(self):
        mutations = DISPATCH[
            DISPATCH.index("perform_deploy_mutations()") : DISPATCH.index(
                "rollback_empty_pre_manifest_journal()"
            )
        ]
        self.assertLess(
            mutations.index("begin_installer_transaction"),
            mutations.index("install_dependencies"),
        )
        self.assertLess(
            mutations.index("install_dependencies"),
            mutations.index("ensure_manifest"),
        )
        self.assertIn("foundation_journal_update package-install", mutations)
        self.assertIn("automatic_initial_rollback", DISPATCH)

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
        for forbidden in (
            "--index-url",
            "--extra-index-url",
            "--trusted-host",
            "--find-links",
            "git+",
            "file:",
            "https://",
            " @ ",
        ):
            self.assertIn(forbidden, LOCK_POLICY)
        self.assertIn("--hash=sha256:", LOCK_POLICY)

    def _run_lock_validation(
        self, lock_path: pathlib.Path
    ) -> subprocess.CompletedProcess[str]:
        command = textwrap.dedent(
            f"""
            set -Eeuo pipefail
            error() {{ printf '%s\\n' "$*" >&2; }}
            INSTALL_SAFE_LOCK_POLICY_SOURCE_ONLY=1
            source {str(LOCK_POLICY_PATH)!r}
            REQUIREMENTS_LOCK={str(lock_path)!r}
            validate_lock
            """
        )
        return subprocess.run(
            ["bash", "-c", command],
            text=True,
            capture_output=True,
            check=False,
        )

    def test_committed_lock_passes_strict_runtime_validator(self):
        result = self._run_lock_validation(ROOT / "requirements.lock")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_lock_validator_rejects_alternate_sources_and_direct_urls(self):
        sha = "a" * 64
        samples = (
            f"--extra-index-url https://evil.example/simple\npkg==1.0 --hash=sha256:{sha}\n",
            f"--trusted-host evil.example\npkg==1.0 --hash=sha256:{sha}\n",
            f"pkg @ https://evil.example/pkg.whl --hash=sha256:{sha}\n",
            f"pkg==1.0 --find-links https://evil.example --hash=sha256:{sha}\n",
            f"pkg==1.0 git+https://evil.example/repo --hash=sha256:{sha}\n",
        )
        for content in samples:
            with self.subTest(content=content.splitlines()[0]):
                with tempfile.TemporaryDirectory() as directory:
                    path = pathlib.Path(directory) / "requirements.lock"
                    path.write_text(content, encoding="utf-8")
                    result = self._run_lock_validation(path)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("requirements.lock", result.stderr)

    def test_uninstall_is_manifest_bounded(self):
        for marker in (
            "foundation_manifest_require",
            "require_owned_path",
            "remove_owned_file",
            "remove_owned_tree",
            "service user ownership отсутствует",
            "post_verify",
            "postgres_expected_marker",
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
        self.assertIn("pre-manifest journal", source)


if __name__ == "__main__":
    unittest.main()
