import pathlib
import re
import subprocess
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class ShellAuditFixTests(unittest.TestCase):
    def test_no_psql_variables_are_passed_through_dash_c(self):
        offenders = []
        for path in ROOT.rglob("*.sh"):
            text = path.read_text(encoding="utf-8")
            if re.search(r"psql[\s\S]{0,700}?\s-c\s+[\"'][\s\S]{0,500}?:[\"']?[A-Za-z_]", text):
                offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual(offenders, [])

    def test_signal_traps_preserve_conventional_exit_codes(self):
        offenders = []
        for path in ROOT.rglob("*.sh"):
            lines = path.read_text(encoding="utf-8").splitlines()
            if any(
                line.lstrip().startswith("trap ") and "EXIT INT TERM" in line
                for line in lines
            ):
                offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual(offenders, [])

    def test_deploy_full_is_not_a_production_entrypoint(self):
        text = (ROOT / "scripts/deploy_full.sh").read_text(encoding="utf-8")
        self.assertIn("Direct execution is forbidden", text)
        result = subprocess.run(
            ["bash", str(ROOT / "scripts/deploy_full.sh"), "--help"],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 64)
        self.assertIn("repository deploy.sh", result.stderr)

    def test_backup_verification_hashes_streamingly(self):
        text = (ROOT / "scripts/ops/verify_backup.sh").read_text(encoding="utf-8")
        self.assertNotIn("read_bytes()", text)
        self.assertIn("handle.read(1024 * 1024)", text)
        self.assertIn("external checksum sidecar is too large", text)

    def test_restore_has_persistent_recovery_journal(self):
        engine = (ROOT / "scripts/ops/production_restore.sh").read_text(encoding="utf-8")
        recovery = (ROOT / "scripts/lib/production_restore_recovery.sh").read_text(encoding="utf-8")
        self.assertIn("production_restore_recovery.sh", engine)
        for marker in (
            "cutover-journal.env",
            "write_cutover_journal production before_old_rename",
            "recover_restore()",
            "fsync_restore_path",
            "interrupted cutover exists; run recover first",
        ):
            self.assertIn(marker, recovery)

    def test_uninstall_refuses_pending_restore_and_purges_transaction_databases(self):
        text = (ROOT / "scripts/uninstall.sh").read_text(encoding="utf-8")
        self.assertIn("assert_no_pending_restore", text)
        self.assertIn("just1kbot_(rb|fail|stg)", text)
        self.assertIn("run restore recover first", text)

    def test_amnezia_uses_global_lock_and_restores_http_rule(self):
        text = (ROOT / "scripts/setup-amnezia-api.sh").read_text(encoding="utf-8")
        self.assertIn("COMMON_LOCK=/run/lock/just1kbot-deploy.lock", text)
        self.assertIn("REMOVED_HTTP=true", text)
        self.assertIn("ufw delete allow 80/tcp", text)
        self.assertIn("another Amnezia domain is already published", text)


if __name__ == "__main__":
    unittest.main()
