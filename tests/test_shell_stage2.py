import pathlib
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


class ShellStage2Tests(unittest.TestCase):
    def test_required_runtime_files_exist(self):
        required = [
            "deploy_full.sh",
            "ops/deploy_application.sh",
            "ops/backup_postgres.sh",
            "ops/verify_backup.sh",
            "ops/restore_rehearsal.sh",
            "ops/just1kbot-restore.sh",
            "setup-amnezia-api.sh",
            "uninstall.sh",
        ]
        self.assertEqual([p for p in required if not (SCRIPTS / p).is_file()], [])

    def test_new_scripts_parse(self):
        for script in (SCRIPTS / "setup-amnezia-api.sh", SCRIPTS / "uninstall.sh"):
            subprocess.run(["bash", "-n", str(script)], check=True)

    def test_uninstall_is_fail_closed(self):
        text = (SCRIPTS / "uninstall.sh").read_text(encoding="utf-8")
        for marker in (
            "PROJECT_DIR=/opt/just1kbot",
            "DELETE JUST1KBOT",
            "just1kbot-backup.timer",
            "just1kbot-healthcheck.timer",
            "backup_before_keep",
            "DROP ROLE IF EXISTS",
            "pg_prepare update",
        ):
            self.assertIn(marker, text)
        self.assertNotIn("--force", text)
        self.assertNotIn("apt-get remove", text)
        self.assertNotIn("/root/.just1kbot-snapshots", text)

    def test_amnezia_is_explicit_and_transactional(self):
        text = (SCRIPTS / "setup-amnezia-api.sh").read_text(encoding="utf-8")
        for marker in (
            "curl --fail --show-error --silent",
            "certbot certonly --webroot",
            "trap rollback EXIT INT TERM",
            "/etc/nginx/conf.d/just1kbot-amnezia-rate-limit.conf",
            "Публичный reverse proxy создаётся только явной командой publish",
        ):
            self.assertIn(marker, text)
        self.assertNotIn("sed -i '/http {/", text)
        self.assertLess(text.index("firewall_add", text.index("publish(){")), text.index("certbot certonly", text.index("publish(){")))
        self.assertIn("begin", text[text.index("unpublish(){"):])


if __name__ == "__main__":
    unittest.main()
