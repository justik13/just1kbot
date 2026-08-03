import pathlib
import re
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


class ShellLayoutTests(unittest.TestCase):
    def test_root_has_only_control_plane_entrypoint(self):
        root_scripts = sorted(path.name for path in ROOT.glob("*.sh"))
        self.assertEqual(root_scripts, ["deploy.sh"])

    def test_required_safe_installer_files_exist(self):
        required = {
            "install_safe.sh",
            "update_from_github.sh",
            "update_from_github_complete.sh",
            "uninstall_foundation.sh",
            "uninstall_entrypoint.sh",
            "preflight_install_state.sh",
            "inspect_install_state.sh",
            "lib/control_plane.sh",
            "lib/control_plane_completion.sh",
            "lib/control_plane_final.sh",
            "lib/installer_foundation.sh",
            "lib/installer_foundation_compat.sh",
            "lib/install_safe_platform.sh",
            "lib/install_safe_release_contract.sh",
            "lib/install_safe_lock_policy.sh",
            "lib/install_safe_runtime.sh",
            "lib/install_safe_tls_policy.sh",
            "lib/install_safe_postgres_ownership.sh",
            "lib/install_safe_proxy_mode.sh",
            "lib/install_safe_activation_policy.sh",
            "lib/install_safe_failure_injection.sh",
            "lib/install_safe_dispatch.sh",
            "lib/uninstall_safe_core.sh",
            "lib/uninstall_safe_actions.sh",
            "lib/uninstall_safe_ownership.sh",
            "lib/postgresql.sh",
            "lib/operational_transaction.sh",
            "ops/deploy_application.sh",
            "ops/doctor.sh",
            "ops/doctor_complete.sh",
            "ops/doctor_json.sh",
            "ops/repair.sh",
            "ops/repair_complete.sh",
            "ops/support_bundle.sh",
            "ops/backup_postgres.sh",
            "ops/verify_backup.sh",
            "ops/restore_rehearsal.sh",
            "ops/just1kbot-restore.sh",
        }
        missing = sorted(name for name in required if not (SCRIPTS / name).is_file())
        self.assertEqual(missing, [])

    def test_all_repository_shell_scripts_parse(self):
        scripts = sorted(ROOT.rglob("*.sh")) + sorted(ROOT.rglob("*.inc"))
        self.assertTrue(scripts)
        for script in scripts:
            with self.subTest(script=script.relative_to(ROOT)):
                subprocess.run(["bash", "-n", str(script)], check=True)

    def test_legacy_deploy_library_is_source_only(self):
        for loader in ("deploy_full.sh", "deploy_full_library.sh"):
            result = subprocess.run(
                ["bash", str(SCRIPTS / loader)],
                text=True,
                capture_output=True,
                check=False,
            )
            with self.subTest(loader=loader):
                self.assertEqual(result.returncode, 64)
                self.assertIn("direct execution is forbidden", result.stderr)

        source_result = subprocess.run(
            [
                "bash",
                "-c",
                f"DEPLOY_FUNCTIONS_ONLY=1 source {str(SCRIPTS / 'deploy_full.sh')!r}; "
                "declare -F parse_args >/dev/null",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(source_result.returncode, 0, source_result.stderr)

    def test_initial_deploy_requires_real_support_username(self):
        core = (SCRIPTS / "lib" / "deploy_core.inc").read_text(encoding="utf-8")
        platform = (SCRIPTS / "lib" / "install_safe_platform.sh").read_text(
            encoding="utf-8"
        )
        example = (ROOT / ".env.example").read_text(encoding="utf-8")
        self.assertIn("SUPPORT_USERNAME", core)
        self.assertIn("SUPPORT_USERNAME", platform)
        self.assertIn("CHANGE_ME_SUPPORT_USERNAME", example)
        self.assertNotIn("SUPPORT_USERNAME='support'", example)

    def test_help_is_non_destructive_without_root(self):
        result = subprocess.run(
            ["bash", str(ROOT / "deploy.sh"), "help"],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Just1kBot", result.stdout)
        self.assertIn("restore-test", result.stdout)
        self.assertIn("install-recover", result.stdout)
        self.assertIn("update", result.stdout)
        self.assertIn("repair", result.stdout)
        self.assertIn("support-bundle", result.stdout)

    def test_direct_dry_run_reaches_safe_installer(self):
        result = subprocess.run(
            ["bash", str(ROOT / "deploy.sh"), "--dry-run"],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("root", result.stderr.lower())

    def test_psql_variables_are_never_embedded_in_dash_c_sql(self):
        variable = re.compile(r""":(?:'|")?[A-Za-z_][A-Za-z0-9_]*"""")
        for script in sorted(ROOT.rglob("*.sh")):
            logical_lines = script.read_text(encoding="utf-8").replace(
                "\\\n", " "
            ).splitlines()
            for line_number, line in enumerate(logical_lines, start=1):
                if "psql" not in line or " -c " not in f" {line} ":
                    continue
                with self.subTest(script=script.relative_to(ROOT), line=line_number):
                    self.assertIsNone(variable.search(line), line)

    def test_signal_cleanup_uses_exit_owned_cleanup(self):
        for relative in (
            "deploy_full.sh",
            "ops/backup_postgres.sh",
            "ops/verify_backup.sh",
            "update_from_github_complete.sh",
            "setup-amnezia-api.sh",
        ):
            text = (SCRIPTS / relative).read_text(encoding="utf-8")
            executable = "\n".join(
                line
                for line in text.splitlines()
                if not line.lstrip().startswith("#")
            )
            with self.subTest(script=relative):
                self.assertRegex(executable, r"trap [A-Za-z_][A-Za-z0-9_]* EXIT")
                self.assertIn("trap 'exit 130' INT", executable)
                self.assertIn("trap 'exit 143' TERM", executable)
                self.assertNotRegex(
                    executable,
                    r"trap [A-Za-z_][A-Za-z0-9_]* EXIT INT TERM",
                )

    def test_postgresql_port_repair_changes_only_database_url_port(self):
        with tempfile.TemporaryDirectory() as directory:
            env_file = pathlib.Path(directory) / ".env"
            env_file.write_text(
                'BOT_TOKEN="keep"\n'
                'DATABASE_URL="postgresql+asyncpg://just1kbot:secret@127.0.0.1:5432/just1kbot_bot"\n'
                'REDIS_URL="redis://keep"\n',
                encoding="utf-8",
            )
            env_file.chmod(0o640)
            before = env_file.stat()
            command = f"""
set -Eeuo pipefail
ENV_FILE={str(env_file)!r}
source {str(SCRIPTS / 'lib/postgresql.sh')!r}
PG_PORT=5433
pg_repair_env_port
"""
            result = subprocess.run(
                ["bash", "-c", command],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                env_file.read_text(encoding="utf-8"),
                'BOT_TOKEN="keep"\n'
                'DATABASE_URL="postgresql+asyncpg://just1kbot:secret@127.0.0.1:5433/just1kbot_bot"\n'
                'REDIS_URL="redis://keep"\n',
            )
            after = env_file.stat()
            self.assertEqual(before.st_mode & 0o777, after.st_mode & 0o777)
            self.assertEqual(before.st_uid, after.st_uid)
            self.assertEqual(before.st_gid, after.st_gid)

    def test_live_tree_permission_check_ignores_only_virtualenv_symlinks(self):
        platform = (SCRIPTS / "lib" / "install_safe_platform.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("Symlink вне virtualenv запрещён", platform)
        self.assertIn('find "$PROJECT_DIR" -xdev -type l', platform)
        self.assertIn('not -path "$VENV_DIR/*"', platform)

    def test_database_collision_preflight_precedes_initial_creation(self):
        platform = (SCRIPTS / "lib" / "install_safe_platform.sh").read_text(
            encoding="utf-8"
        )
        function = platform[
            platform.index("setup_postgresql_initial()") : platform.index(
                "record_existing_postgres()"
            )
        ]
        self.assertLess(
            function.index("preflight_postgres_names_absent"),
            function.index("pg_prepare_initial_database"),
        )

    def test_first_install_rollback_does_not_start_absent_service(self):
        runtime = (SCRIPTS / "lib" / "install_safe_runtime.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "previous_service=absent start_not_attempted=true", runtime
        )
        absent_branch = runtime.index(
            'if [[ ! -f "$ROLLBACK_SNAPSHOT/systemd.service" ]]'
        )
        next_start = runtime.index("service_call start", absent_branch)
        branch_return = runtime.index("return 1", absent_branch)
        self.assertLess(branch_return, next_start)


if __name__ == "__main__":
    unittest.main()
