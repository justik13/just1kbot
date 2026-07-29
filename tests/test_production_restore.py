import pathlib
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "ops" / "restore_production.sh"


class ProductionRestoreContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = SCRIPT.read_text()
        cls.backup = (ROOT / "ops" / "backup_postgres.sh").read_text()
        cls.lock_helper = (ROOT / "ops" / "hold_restore_advisory_lock.py").read_text()
        cls.validator = (ROOT / "ops" / "validate_restore_candidate.py").read_text()

    def test_confirmation_is_exact_and_noninteractive(self):
        self.assertIn("--confirm-production-restore", self.text); self.assertNotIn("read -p", self.text)

    def test_root_and_three_nonblocking_file_locks(self):
        self.assertIn("$EUID -eq 0", self.text)
        for value in ("just1kbot-restore.lock", "just1kbot-deploy.lock", "just1kbot-backup.lock", "flock -n 9"):
            self.assertIn(value, self.text)

    def test_persistent_advisory_helper_holds_until_stdin_closes(self):
        self.assertIn("pg_advisory_lock", self.lock_helper)
        self.assertIn("sys.stdin.buffer.read", self.lock_helper)
        self.assertIn("pg_advisory_unlock", self.lock_helper)
        self.assertIn("start_advisory_lock", self.text); self.assertIn("stop_advisory_lock", self.text)

    def test_artifact_is_rechecked_and_private_copy_is_made(self):
        self.assertGreaterEqual(self.text.count('fingerprint "$canonical"'), 3)
        self.assertIn('install -m600 "$canonical" "$workspace/pinned.tar.age"', self.text)

    def test_key_mismatch_is_safe(self):
        self.assertIn("encryption_key_match=false", self.text); self.assertNotIn("echo $current_key", self.text)

    def test_candidate_only_migration_and_read_only_validator(self):
        self.assertIn('[[ $candidate_db == just1kbot_candidate_* ]]', self.text)
        self.assertIn('DATABASE_URL="$RESTORE_CANDIDATE_DATABASE_URL"', self.text)
        self.assertIn("SET TRANSACTION READ ONLY", self.validator)
        for forbidden in ("bot.main", "YooKassa", "amnezia_client", ".commit("):
            self.assertNotIn(forbidden, self.validator)

    def test_recovery_helper_is_fail_closed(self):
        self.assertIn("recover_original_service_before_swap", self.text)
        self.assertIn("requires_manual_recovery", self.text)
        self.assertIn("CRITICAL_RECOVERY_EXIT=43", self.text)
        self.assertNotIn("service_stopped=false", self.text)

    def test_strict_manifest_states_and_schema(self):
        for state in ("in_progress", "failed_safe", "success", "rolled_back", "requires_manual_recovery", "rollback_failed", "finalized"):
            self.assertIn(state, self.text)
        self.assertIn("set(d)!=required", self.text); self.assertIn("operation_database_state_mismatch", self.text)

    def test_exact_backup_result_and_persistent_pin(self):
        self.assertIn("BACKUP_RESULT_FILE", self.backup); self.assertIn("BACKUP_ARTIFACT_PIN", self.backup)
        self.assertIn('pin_file="$final.pin"', self.backup); self.assertIn('[[ -z "$old" || -f "$old.pin" ]]', self.backup)
        self.assertIn("parse_backup_result", self.text); self.assertNotIn('before=$(find "$BACKUP_DIR"', self.text)

    def test_finalize_strictly_verifies_both_artifacts(self):
        body = self.text[self.text.index("finalize(){"):self.text.index("mode=restore")]
        self.assertEqual(body.count("verify_recovery_artifact"), 2)
        self.assertIn("finalize_backup_path", body); self.assertLess(body.rindex("health || return 1"), body.index('dropdb --maintenance-db'))

    def test_swap_and_rollback_preserve_quarantines(self):
        self.assertIn('rename_db "$PRODUCTION_DB" "$previous_db"', self.text)
        self.assertIn('rename_db "$PRODUCTION_DB" "$failed_db"', self.text)
        self.assertIn('rename_db "$previous_db" "$PRODUCTION_DB"', self.text)
        self.assertNotIn('dropdb "$PRODUCTION_DB"', self.text)

    def test_manual_rollback_only_recovery_states(self):
        self.assertIn('[[ ${x[0]} == in_progress || ${x[0]} == requires_manual_recovery || ${x[0]} == rollback_failed ]]', self.text)

    def test_separate_free_space_checks(self):
        self.assertIn('"$workspace" "$BACKUP_DIR"', self.text); self.assertIn("SHOW data_directory", self.text)

    def test_shell_parses_and_secrets_are_not_traced(self):
        subprocess.run(["bash", "-n", SCRIPT], check=True); self.assertNotIn("set -x", self.text)


if __name__ == "__main__": unittest.main()
