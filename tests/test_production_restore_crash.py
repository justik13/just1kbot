import pathlib
import subprocess
import tempfile
import textwrap
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
ENGINE = ROOT / "scripts" / "ops" / "production_restore.sh"
CRASH = ROOT / "scripts" / "lib" / "production_restore_crash.sh"
RECOVERY_CLEANUP = (
    ROOT / "scripts" / "lib" / "production_restore_recovery_cleanup.sh"
)
ACTIONS = ROOT / "scripts" / "lib" / "production_restore_actions.sh"


class ProductionRestoreCrashTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = ENGINE.read_text(encoding="utf-8")
        cls.crash = CRASH.read_text(encoding="utf-8")
        cls.recovery_cleanup = RECOVERY_CLEANUP.read_text(encoding="utf-8")
        cls.actions = ACTIONS.read_text(encoding="utf-8")

    def test_recovery_cleanup_override_is_loaded_last_and_hardened(self):
        self.assertIn("production_restore_recovery_cleanup.sh; do", self.engine)
        self.assertLess(
            self.engine.index("production_restore_actions.sh"),
            self.engine.index("production_restore_crash.sh"),
        )
        self.assertLess(
            self.engine.index("production_restore_crash.sh"),
            self.engine.index("production_restore_recovery_cleanup.sh"),
        )
        self.assertIn("unsafe library mode", self.engine)
        self.assertIn("library is not root-owned", self.engine)

    def test_every_database_swap_has_a_durable_journal_phase(self):
        for marker in (
            "begin_cutover_journal production before_old_rename",
            "update_cutover_journal old_renamed",
            "update_cutover_journal new_renamed",
            "begin_cutover_journal rollback_previous before_live_rename",
            "update_cutover_journal rollback_live_renamed",
            "update_cutover_journal rollback_promoted",
            "begin_cutover_journal manual_return before_previous_rename",
            "update_cutover_journal manual_previous_renamed",
            "update_cutover_journal manual_restored_promoted",
            "sync_file_and_parent \"$JOURNAL_STATE\"",
        ):
            self.assertIn(marker, self.crash)

    def test_manual_rollback_state_failure_has_recovery_path(self):
        for marker in (
            'CUTOVER_PHASE="manual_rollback_swapped"',
            'CUTOVER_PHASE="manual_restored_returned"',
            "RECOVERY_ACTION=true",
            "recover_interrupted_cutover",
        ):
            self.assertIn(marker, self.actions)
        for marker in (
            '[[ "$CUTOVER_PHASE" == manual_rollback_swapped',
            'write_active_state rolled_back "$FAILED_DB"',
            "return_restored_database_after_manual_rollback",
            'write_active_state active ""',
        ):
            self.assertIn(marker, self.recovery_cleanup)

    def test_ambiguous_recovery_never_mutates_databases_in_exit_cleanup(self):
        recovery_branch = self.recovery_cleanup.split(
            'if [[ "$RECOVERY_ACTION" == true ]]', 1
        )[1].split("fi\n\n    if (( rc != 0 ))", 1)[0]
        self.assertNotIn("admin_dropdb", recovery_branch)
        self.assertNotIn("rename_database", recovery_branch)
        self.assertNotIn("clear_cutover_journal", recovery_branch)
        self.assertIn("restore_timer_states", recovery_branch)

    def test_finalize_is_idempotent_after_preserved_database_drop(self):
        for marker in (
            "preserved database is already absent; completing interrupted finalize",
            "preserved database still exists after finalize drop",
            '[[ "$preserved" != "$LIVE_DATABASE" ]]',
        ):
            self.assertIn(marker, self.actions)

    def test_return_restored_database_order_is_executable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            log = root / "calls"
            journal = root / "journal"
            command = textwrap.dedent(
                f"""
                set -Eeuo pipefail
                RESTORE_FUNCTIONS_ONLY=1 source {str(ENGINE)!r}
                LIVE_DATABASE=just1kbot_bot
                ROLLBACK_DB=just1kbot_rb_20260801000000_1
                FAILED_DB=just1kbot_fail_20260801000000_1
                JOURNAL_STATE={str(journal)!r}
                record() {{ printf '%s\\n' "$*" >> {str(log)!r}; }}
                begin_cutover_journal() {{ JOURNAL_OPERATION=$1; record "journal begin $1 $2"; }}
                update_cutover_journal() {{ record "journal update $1"; }}
                database_allow_connections() {{ record "allow $1 $2"; }}
                terminate_database_connections() {{ record "terminate $1"; }}
                rename_database() {{ record "rename $1 $2"; }}
                set_database_owner() {{ record "owner $1"; }}
                return_restored_database_after_manual_rollback
                """
            )
            result = subprocess.run(
                ["bash", "-c", command],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                log.read_text(encoding="utf-8").splitlines(),
                [
                    "journal begin manual_return before_previous_rename",
                    "allow just1kbot_bot false",
                    "terminate just1kbot_bot",
                    "allow just1kbot_fail_20260801000000_1 false",
                    "terminate just1kbot_fail_20260801000000_1",
                    "rename just1kbot_bot just1kbot_rb_20260801000000_1",
                    "journal update manual_previous_renamed",
                    "rename just1kbot_fail_20260801000000_1 just1kbot_bot",
                    "journal update manual_restored_promoted",
                    "owner just1kbot_bot",
                    "allow just1kbot_bot true",
                    "journal update manual_return_complete",
                ],
            )


if __name__ == "__main__":
    unittest.main()
