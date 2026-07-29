import os
import pathlib
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "ops" / "restore_production.sh"


class ProductionRestoreContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = SCRIPT.read_text()
        cls.validator = (ROOT / "ops" / "validate_restore_candidate.py").read_text()

    def test_confirmation_is_exact_and_noninteractive(self):
        self.assertIn("--confirm-production-restore", self.text)
        self.assertNotIn("read -p", self.text)

    def test_distinct_nonblocking_locks(self):
        for value in ("just1kbot-restore.lock", "just1kbot-deploy.lock", "just1kbot-backup.lock", "flock -n 9"):
            self.assertIn(value, self.text)

    def test_artifact_is_rechecked_and_pinned(self):
        self.assertGreaterEqual(self.text.count('fingerprint "$canonical"'), 3)
        self.assertIn('install -m 600 "$canonical" "$workspace/pinned.tar.age"', self.text)

    def test_key_mismatch_is_safe(self):
        self.assertIn("encryption_key_match=false", self.text)
        self.assertNotIn("hashlib.sha256(current_key", self.text)

    def test_candidate_only_migration_guard(self):
        self.assertIn('[[ $candidate_db == just1kbot_candidate_* ]]', self.text)
        self.assertIn('DATABASE_URL="$RESTORE_CANDIDATE_DATABASE_URL"', self.text)

    def test_validator_has_no_external_clients_or_writes(self):
        for forbidden in ("bot.main", "YooKassa", "amnezia_client", ".commit(", ".flush(", "Telegram"):
            self.assertNotIn(forbidden, self.validator)
        self.assertIn("SET TRANSACTION READ ONLY", self.validator)

    def test_emergency_backup_is_retention_exempt_and_rehearsed(self):
        self.assertIn('BACKUP_SKIP_RETENTION=true "$BACKUP_COMMAND"', self.text)
        self.assertIn('"$REHEARSAL_COMMAND" "$emergency_artifact"', self.text)

    def test_swap_never_drops_production_or_previous(self):
        self.assertIn('rename_db "$PRODUCTION_DB" "$previous_db"', self.text)
        self.assertNotIn('dropdb "$PRODUCTION_DB"', self.text)
        main_flow = self.text[: self.text.index("finalize_operation()")]
        self.assertNotIn('dropdb --maintenance-db="$MAINTENANCE_DB" "$previous_db"', main_flow)

    def test_rollback_quarantines_failed_candidate(self):
        self.assertIn('rename_db "$PRODUCTION_DB" "$failed"', self.text)
        self.assertIn('rename_db "$previous" "$PRODUCTION_DB"', self.text)
        self.assertIn("CRITICAL_ROLLBACK_EXIT=42", self.text)

    def test_interrupted_modes_use_manifest_names(self):
        self.assertIn("--inspect-incomplete", self.text)
        self.assertIn("--rollback-operation", self.text)
        self.assertNotIn("--previous-database", self.text)
        self.assertNotIn("--candidate-database", self.text)

    def test_finalize_has_all_destructive_guards(self):
        for value in ("finalize_safety_window_not_elapsed", "emergency_backup_missing_or_changed", "finalize_backup_not_created", "--confirm-delete-previous"):
            self.assertIn(value, self.text)

    def test_shell_parses(self):
        subprocess.run(["bash", "-n", SCRIPT], check=True)

    def test_no_secret_debugging(self):
        self.assertNotIn("set -x", self.text)
        self.assertNotIn("echo $PGPASSWORD", self.text)
        self.assertNotIn("echo $current_key", self.text)


if __name__ == "__main__":
    unittest.main()
