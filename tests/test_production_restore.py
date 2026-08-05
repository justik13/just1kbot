import pathlib
import subprocess
import tempfile
import textwrap
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "scripts" / "ops" / "just1kbot-restore.sh"
RESTORE = ROOT / "scripts" / "ops" / "production_restore.sh"
RESTORE_LIBRARIES = (
    ROOT / "scripts" / "lib" / "production_restore_core.sh",
    ROOT / "scripts" / "lib" / "production_restore_runtime.sh",
    ROOT / "scripts" / "lib" / "production_restore_actions.sh",
)
VERIFY = ROOT / "scripts" / "ops" / "verify_backup.sh"
CONTROL = ROOT / "scripts" / "lib" / "control_plane.sh"


class ProductionRestoreContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.wrapper = WRAPPER.read_text(encoding="utf-8")
        cls.restore = "\n".join(
            [RESTORE.read_text(encoding="utf-8")]
            + [path.read_text(encoding="utf-8") for path in RESTORE_LIBRARIES]
        )
        cls.verify = VERIFY.read_text(encoding="utf-8")
        cls.control = CONTROL.read_text(encoding="utf-8")

    def test_help_is_non_destructive_without_root(self):
        result = subprocess.run(
            ["bash", str(WRAPPER), "--help"],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Production lifecycle", result.stdout)
        self.assertIn("rollback", result.stdout)
        self.assertIn("finalize", result.stdout)

    def test_legacy_artifact_invocation_stays_rehearsal_only(self):
        self.assertIn('exec bash "$rehearsal" "$@"', self.wrapper)
        self.assertIn('production|status|rollback|finalize', self.wrapper)
        self.assertNotIn("dropdb", self.wrapper)
        self.assertNotIn("systemctl stop", self.wrapper)
        artifact_branch = self.wrapper.rindex("    *)")
        self.assertNotIn("resolve_engine", self.wrapper[artifact_branch:])

    def test_production_restore_is_fail_closed(self):
        for marker in (
            "--yes requires --expected-sha256",
            "a previous restore transaction must be finalized",
            "backup DB_ENCRYPTION_KEY does not match production",
            "insufficient free space",
            "fresh pre-cutover backup created and strictly verified",
            "restored production failed readiness",
        ):
            self.assertIn(marker, self.restore)
        self.assertNotIn('source "$ENV_FILE"', self.restore)
        self.assertNotIn(
            'dropdb --force --if-exists --maintenance-db=postgres "$LIVE_DATABASE"',
            self.restore,
        )

    def test_absent_database_check_accepts_missing_database(self):
        result = self._run_function_test(
            """
            database_exists() { return 1; }
            assert_database_absent just1kbot_stg_20260801000000_1
            """
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_absent_database_check_rejects_existing_database(self):
        result = self._run_function_test(
            """
            database_exists() { return 0; }
            if assert_database_absent just1kbot_stg_20260801000000_1; then
                printf 'existing database was incorrectly accepted\n' >&2
                exit 1
            fi
            """
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_staging_precedes_writer_stop_and_cutover(self):
        staging = self.restore.index("restore_staging_database")
        confirmation = self.restore.index("confirm_production_cutover", staging)
        pause = self.restore.index("pause_runtime", confirmation)
        safety_backup = self.restore.index("create_final_pre_cutover_backup", pause)
        cutover = self.restore.index("database_cutover", safety_backup)
        health = self.restore.index("wait_for_application_health", cutover)
        self.assertLess(staging, confirmation)
        self.assertLess(confirmation, pause)
        self.assertLess(pause, safety_backup)
        self.assertLess(safety_backup, cutover)
        self.assertLess(cutover, health)

    def test_previous_database_requires_separate_finalize(self):
        self.assertIn('rename_database "$LIVE_DATABASE" "$ROLLBACK_DB"', self.restore)
        self.assertIn(
            "run 'just1kbot-restore.sh status', then choose rollback or finalize",
            self.restore,
        )
        self.assertIn('admin_dropdb "$preserved"', self.restore)
        self.assertIn('[[ "$preserved" != "$LIVE_DATABASE" ]]', self.restore)

    def test_dump_is_copied_to_postgres_private_workspace(self):
        for marker in (
            "POSTGRES_WORK_DIR=$(mktemp -d /var/lib/postgresql/just1kbot-production-restore.",
            'chown postgres:postgres "$POSTGRES_WORK_DIR"',
            'install -o postgres -g postgres -m 0600',
            '"$POSTGRES_WORK_DIR/dump.custom"',
        ):
            self.assertIn(marker, self.restore)
        rehearsal = (
            ROOT / "scripts" / "ops" / "restore_rehearsal.sh"
        ).read_text(encoding="utf-8")
        self.assertIn("/var/lib/postgresql/just1kbot-rehearsal.", rehearsal)
        self.assertIn('restore_dump="$postgres_work/dump.custom"', rehearsal)

    def test_verifier_extracts_config_only_to_private_extract_dir(self):
        self.assertIn('mkdir -m 700 "$extract_dir"', self.verify)
        self.assertIn(
            'install -m 600 "$tmpdir/extracted/config.env" "$extract_dir/config.env"',
            self.verify,
        )

    def test_control_plane_exposes_full_restore_lifecycle(self):
        for marker in (
            "restore-production",
            "restore-status",
            "restore-recover",
            "restore-rollback",
            "restore-finalize",
            "Restore production",
        ):
            self.assertIn(marker, self.control)
        production_case = self.control[
            self.control.index("        restore-production)") : self.control.index(
                "        restore-status)"
            )
        ]
        self.assertIn(
            'call_script ops/just1kbot-restore.sh production "$@"',
            production_case,
        )
        self.assertNotIn("run_locked_script", production_case)

    def _run_function_test(self, body: str) -> subprocess.CompletedProcess[str]:
        command = textwrap.dedent(
            f"""
            set -Eeuo pipefail
            RESTORE_FUNCTIONS_ONLY=1 source {str(RESTORE)!r}
            {body}
            """
        )
        return subprocess.run(
            ["bash", "-c", command],
            text=True,
            capture_output=True,
            check=False,
        )

    def test_database_cutover_order_is_executable(self):
        with tempfile.TemporaryDirectory() as directory:
            log = pathlib.Path(directory) / "calls"
            result = self._run_function_test(
                f"""
                LIVE_DATABASE=just1kbot_bot
                STAGING_DB=just1kbot_stg_20260801000000_1
                ROLLBACK_DB=just1kbot_rb_20260801000000_1
                LIVE_ROLE=just1kbot
                record() {{ printf '%s\\n' "$*" >> {str(log)!r}; }}
                database_allow_connections() {{ record "allow $1 $2"; }}
                terminate_database_connections() {{ record "terminate $1"; }}
                rename_database() {{ record "rename $1 $2"; }}
                set_database_owner() {{ record "owner $1"; }}
                database_cutover
                """
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                log.read_text(encoding="utf-8").splitlines(),
                [
                    "allow just1kbot_bot false",
                    "terminate just1kbot_bot",
                    "allow just1kbot_stg_20260801000000_1 false",
                    "terminate just1kbot_stg_20260801000000_1",
                    "rename just1kbot_bot just1kbot_rb_20260801000000_1",
                    "rename just1kbot_stg_20260801000000_1 just1kbot_bot",
                    "owner just1kbot_bot",
                    "allow just1kbot_bot true",
                ],
            )

    def test_database_rollback_order_is_executable(self):
        with tempfile.TemporaryDirectory() as directory:
            log = pathlib.Path(directory) / "calls"
            result = self._run_function_test(
                f"""
                LIVE_DATABASE=just1kbot_bot
                ROLLBACK_DB=just1kbot_rb_20260801000000_1
                LIVE_ROLE=just1kbot
                FAILED_DB=just1kbot_fail_20260801000000_1
                record() {{ printf '%s\\n' "$*" >> {str(log)!r}; }}
                database_allow_connections() {{ record "allow $1 $2"; }}
                terminate_database_connections() {{ record "terminate $1"; }}
                rename_database() {{ record "rename $1 $2"; }}
                set_database_owner() {{ record "owner $1"; }}
                database_rollback_to_previous "$FAILED_DB"
                """
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                log.read_text(encoding="utf-8").splitlines(),
                [
                    "allow just1kbot_bot false",
                    "terminate just1kbot_bot",
                    "allow just1kbot_rb_20260801000000_1 false",
                    "terminate just1kbot_rb_20260801000000_1",
                    "rename just1kbot_bot just1kbot_fail_20260801000000_1",
                    "rename just1kbot_rb_20260801000000_1 just1kbot_bot",
                    "owner just1kbot_bot",
                    "allow just1kbot_bot true",
                ],
            )


if __name__ == "__main__":
    unittest.main()
