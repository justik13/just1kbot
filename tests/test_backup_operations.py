import os
import pathlib
import shutil
import stat
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
OPS = ROOT / "ops"


class BackupOperationsContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.backup = (OPS / "backup_postgres.sh").read_text()
        cls.verify = (OPS / "verify_backup.sh").read_text()
        cls.rehearsal = (OPS / "restore_rehearsal.sh").read_text()
        cls.deploy = (ROOT / "deploy.sh").read_text()

    def test_backup_requires_age_recipient(self):
        self.assertIn("BACKUP_AGE_RECIPIENT is missing or invalid", self.backup)

    def test_plaintext_config_is_cleaned_after_success(self):
        self.assertIn("rm -rf -- \"$tmpdir\"", self.backup)

    def test_plaintext_config_is_cleaned_after_error(self):
        self.assertIn("trap finish EXIT INT TERM", self.backup)

    def test_dump_list_precedes_publication(self):
        self.assertLess(self.backup.index("pg_restore --list"), self.backup.index('mv -- "$BACKUP_DIR/.${name}.partial"'))

    def test_corrupt_dump_cannot_publish(self):
        self.assertIn('pg_restore --list "$tmpdir/dump.custom"', self.backup)

    def test_encryption_failure_cannot_publish(self):
        self.assertLess(self.backup.index('age -r "$BACKUP_AGE_RECIPIENT"'), self.backup.index('mv -- "$BACKUP_DIR/.${name}.partial"'))

    def test_retention_follows_all_required_publication(self):
        self.assertLess(self.backup.index("required off-site publication failed"), self.backup.index("mapfile -t expired"))

    def test_nonblocking_exclusive_lock(self):
        self.assertIn("flock -n 9", self.backup)

    def test_atomic_local_rename(self):
        self.assertIn('.partial\" \"$final\"', self.backup)

    def test_checksum_mismatch_is_detected(self):
        self.assertIn("external checksum mismatch", self.verify)

    def test_wrong_identity_is_rejected(self):
        self.assertIn("decryption failed", self.verify)

    def test_malicious_paths_are_rejected(self):
        self.assertIn("p.is_absolute() or '..' in p.parts", self.verify)

    def test_links_are_rejected(self):
        self.assertIn("not member.isfile()", self.verify)

    def test_unknown_format_is_rejected(self):
        self.assertIn("manifest['format_version'] != 1", self.verify)

    def test_missing_config_is_rejected(self):
        self.assertIn("'config.env'", self.verify)

    def test_scripts_do_not_echo_secret_values(self):
        for text in (self.backup, self.verify, self.rehearsal):
            self.assertNotIn("set -x", text)

    def test_offsite_checksum_is_verified(self):
        self.assertIn('sha256sum "$OFFSITE_DIR/.${name}.partial"', self.backup)

    def test_required_offsite_failure_is_fatal(self):
        self.assertIn("required off-site publication failed", self.backup)

    def test_rehearsal_creates_separate_database(self):
        self.assertIn('test_db="just1kbot_rehearsal_', self.rehearsal)

    def test_rehearsal_never_targets_production_database(self):
        self.assertNotIn("just1kbot_bot", self.rehearsal)

    def test_rehearsal_database_removed_after_success(self):
        self.assertIn('dropdb --if-exists "$test_db"', self.rehearsal)

    def test_rehearsal_database_removed_after_error(self):
        self.assertIn("trap cleanup EXIT INT TERM", self.rehearsal)

    def test_keep_option_only_controls_test_database(self):
        self.assertIn("--keep-test-db", self.rehearsal)
        self.assertIn('[[ "$test_db" == just1kbot_rehearsal_* ]]', self.rehearsal)

    def test_destructive_restore_is_removed(self):
        wrapper = (OPS / "just1kbot-restore.sh").read_text()
        self.assertNotIn("dropdb", wrapper)
        self.assertNotIn("systemctl stop", wrapper)

    def test_all_shell_scripts_parse(self):
        for script in [ROOT / "deploy.sh", *OPS.glob("*.sh")]:
            subprocess.run(["bash", "-n", str(script)], check=True)

    def test_deploy_uses_systemd_timer_not_backup_cron(self):
        self.assertIn("Persistent=true", self.deploy)
        self.assertNotIn('echo "0 3 * * * /usr/local/bin/just1kbot-backup.sh"', self.deploy)


@unittest.skipUnless(os.getenv("RUN_BACKUP_INTEGRATION") == "1", "requires PostgreSQL and age")
class RealBackupRehearsalIntegrationTest(unittest.TestCase):
    def test_real_dump_verify_and_isolated_restore(self):
        required = ("age", "age-keygen", "pg_dump", "pg_restore", "psql", "createdb", "dropdb")
        if any(shutil.which(command) is None for command in required):
            self.skipTest("backup tools are unavailable")
        url = os.environ["BACKUP_TEST_DATABASE_URL"]
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            identity, envfile, backups = root / "identity", root / ".env", root / "backups"
            subprocess.run(["age-keygen", "-o", identity], check=True, capture_output=True, text=True)
            recipient = subprocess.check_output(["age-keygen", "-y", identity], text=True).strip()
            envfile.write_text(f"DATABASE_URL='{url}'\nDB_ENCRYPTION_KEY='canary-key'\nREDIS_URL='redis://test'\nBOT_TOKEN='canary-token'\n")
            env = os.environ | {"BACKUP_AGE_RECIPIENT": recipient, "AGE_IDENTITY_FILE": str(identity),
                                "ENV_FILE": str(envfile), "BACKUP_DIR": str(backups),
                                "BACKUP_LOCK_FILE": str(root / "backup.lock"), "PROJECT_DIR": str(ROOT),
                                "VERIFY_BACKUP": str(OPS / "verify_backup.sh"),
                                "REHEARSAL_DATABASE_URL": url,
                                "REHEARSAL_CRITICAL_TABLES": "backup_rehearsal_data"}
            subprocess.run(["psql", url, "-c", "CREATE TABLE IF NOT EXISTS backup_rehearsal_data(value text); INSERT INTO backup_rehearsal_data VALUES ('preserved');"], check=True, capture_output=True)
            subprocess.run(["psql", url, "-c", "CREATE TABLE IF NOT EXISTS alembic_version(version_num varchar(32)); INSERT INTO alembic_version SELECT 'test' WHERE NOT EXISTS (SELECT 1 FROM alembic_version);"], check=True, capture_output=True)
            completed = subprocess.run([OPS / "backup_postgres.sh"], env=env, text=True, capture_output=True, check=True)
            self.assertNotIn("canary-token", completed.stdout + completed.stderr)
            artifact = next(backups.glob("*.tar.age"))
            subprocess.run([OPS / "verify_backup.sh", artifact], env=env, check=True, capture_output=True)
            rehearsal = subprocess.run([OPS / "restore_rehearsal.sh", artifact], env=env, text=True, capture_output=True, check=True)
            self.assertIn("success=success", rehearsal.stdout)
            dbname = rehearsal.stdout.split("rehearsal_database=", 1)[1].split()[0]
            exists = subprocess.check_output(["psql", url, "-Atc", f"SELECT count(*) FROM pg_database WHERE datname='{dbname}'"], text=True).strip()
            self.assertEqual(exists, "0")
            self.assertEqual(list(backups.glob(".backup-work.*")), [])
