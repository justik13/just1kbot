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
        self.assertEqual(
            [path for path in required if not (SCRIPTS / path).is_file()],
            [],
        )

    def test_new_scripts_parse(self):
        for script in (
            SCRIPTS / "setup-amnezia-api.sh",
            SCRIPTS / "uninstall.sh",
        ):
            subprocess.run(["bash", "-n", str(script)], check=True)

    def test_uninstall_is_fail_closed_and_interactive(self):
        text = (SCRIPTS / "uninstall.sh").read_text(encoding="utf-8")
        for marker in (
            "PROJECT_DIR=/opt/just1kbot",
            "DELETE JUST1KBOT",
            "Выберите режим удаления",
            "just1kbot-backup.timer",
            "just1kbot-healthcheck.timer",
            "backup_before_keep",
            "purge_redis",
            "REDISCLI_AUTH",
            "DROP ROLE IF EXISTS",
            "pg_prepare update",
        ):
            self.assertIn(marker, text)
        self.assertNotIn("--force", text)
        self.assertNotIn("apt-get remove", text)
        self.assertNotIn("/root/.just1kbot-snapshots", text)

    def test_amnezia_is_explicit_interactive_and_transactional(self):
        text = (SCRIPTS / "setup-amnezia-api.sh").read_text(
            encoding="utf-8"
        )
        for marker in (
            "ACTION=menu",
            "Публичный reverse proxy создаётся только явным действием publish",
            "curl --fail --show-error --silent",
            "certbot certonly --webroot",
            "trap rollback EXIT INT TERM",
            "CERT_CREATED",
            "ADDED_HTTP",
            "/etc/nginx/conf.d/just1kbot-amnezia-rate-limit.conf",
            "Опубликовать HTTPS reverse proxy",
        ):
            self.assertIn(marker, text)
        self.assertNotIn("sed -i '/http {/", text)
        publish = text.index("publish(){")
        self.assertLess(
            text.index("firewall_add", publish),
            text.index("certbot certonly", publish),
        )
        self.assertIn("begin", text[text.index("unpublish(){"):])

    def test_root_menu_targets_interactive_and_locked_operations(self):
        menu = (ROOT / "deploy.sh").read_text(encoding="utf-8")
        self.assertIn("run_script setup-amnezia-api.sh", menu)
        self.assertIn("run_script uninstall.sh", menu)
        self.assertIn("run_locked_script deploy.sh --backup", menu)
        self.assertIn("run_locked_script deploy.sh --restore", menu)
        self.assertIn('DEPLOY_FUNCTIONS_ONLY:-0}', menu)


if __name__ == "__main__":
    unittest.main()
