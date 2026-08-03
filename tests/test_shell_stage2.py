import pathlib
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


class ShellStage2Tests(unittest.TestCase):
    def test_required_runtime_files_exist(self):
        required = [
            "deploy_full.sh",
            "deploy_full_library.sh",
            "ops/deploy_application.sh",
            "ops/backup_postgres.sh",
            "ops/verify_backup.sh",
            "ops/restore_rehearsal.sh",
            "ops/just1kbot-restore.sh",
            "ops/production_restore.sh",
            "lib/production_restore_core.sh",
            "lib/production_restore_runtime.sh",
            "lib/production_restore_actions.sh",
            "lib/production_restore_input.sh",
            "lib/production_restore_crash.sh",
            "lib/production_restore_recovery_cleanup.sh",
            "setup-amnezia-api.sh",
            "uninstall.sh",
            "uninstall_entrypoint.sh",
            "preflight_uninstall_resources.sh",
            "verify_uninstall_state.sh",
            "inspect_install_state.sh",
            "lib/installer_diagnostics.sh",
            "preflight_install_state.sh",
        ]
        self.assertEqual(
            [path for path in required if not (SCRIPTS / path).is_file()],
            [],
        )

    def test_new_scripts_parse(self):
        for script in (
            SCRIPTS / "setup-amnezia-api.sh",
            SCRIPTS / "uninstall.sh",
            SCRIPTS / "uninstall_entrypoint.sh",
            SCRIPTS / "preflight_uninstall_resources.sh",
            SCRIPTS / "verify_uninstall_state.sh",
            SCRIPTS / "inspect_install_state.sh",
            SCRIPTS / "lib" / "installer_diagnostics.sh",
            SCRIPTS / "preflight_install_state.sh",
            SCRIPTS / "ops" / "production_restore.sh",
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
            "pg_select_cluster",
            "preflight_restore_state",
            "cutover-journal.env",
            "^just1kbot_(stg|rb|fail)_",
            "acquire_uninstall_lock",
            "pause_operational_work",
            "preflight_purge",
            "PURGE_REDIS_CONNECTION",
            "run_resource_preflight",
            "nginx_site_has_expected_markers",
            "site автоматически восстановлен",
        ):
            self.assertIn(marker, text)
        self.assertNotIn("--force", text)
        self.assertNotIn("apt-get remove", text)
        self.assertNotIn("/root/.just1kbot-snapshots", text)
        self.assertNotIn("mapfile -t connection < <(redis_connection)", text)
        self.assertNotIn("setup-amnezia-api.sh", text)
        self.assertNotIn("ufw ", text)
        self.assertNotIn("certbot delete", text)

        main = text[text.index("main() {") :]
        self.assertLess(main.index("run_resource_preflight"), main.index("preflight_purge"))
        self.assertLess(main.index("preflight_purge"), main.index("pause_operational_work"))
        self.assertLess(main.index("pause_operational_work"), main.index("stop_units"))

    def test_amnezia_script_remains_standalone_and_transactional(self):
        text = (SCRIPTS / "setup-amnezia-api.sh").read_text(
            encoding="utf-8"
        )
        for marker in (
            "ACTION=menu",
            "Публичный reverse proxy создаётся только явным действием publish",
            "curl --fail --show-error --silent",
            "certbot certonly --webroot",
            "trap rollback EXIT",
            "trap 'exit 130' INT",
            "trap 'exit 143' TERM",
            "CERT_CREATED",
            "ADDED_HTTP",
            "REMOVED_HTTP",
            "OPERATION_LOCK=/run/lock/just1kbot-deploy.lock",
            "another managed Amnezia domain",
            "UFW explicitly denies",
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
        self.assertIn("ufw delete allow 80/tcp", text[text.index("unpublish(){"):])

    def test_root_menu_excludes_standalone_amnezia_utility(self):
        menu = (ROOT / "deploy.sh").read_text(encoding="utf-8")
        self.assertNotIn("run_script setup-amnezia-api.sh", menu)
        self.assertNotIn("amnezia)", menu)
        self.assertIn("Standalone setup-amnezia-api.sh", menu)
        self.assertIn("run_script uninstall_entrypoint.sh", menu)
        self.assertIn("run_locked_script deploy.sh --backup", menu)
        self.assertIn(
            'run_locked_script ops/just1kbot-restore.sh rehearsal "$1"',
            menu,
        )
        self.assertIn(
            'run_script ops/just1kbot-restore.sh production "$@"',
            menu,
        )
        self.assertIn(
            "run_script ops/just1kbot-restore.sh recover",
            menu,
        )
        self.assertIn('DEPLOY_FUNCTIONS_ONLY:-0}', menu)
        self.assertIn("inspect_deploy_state --require-safe", menu)


if __name__ == "__main__":
    unittest.main()
