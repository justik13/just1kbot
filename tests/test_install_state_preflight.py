import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
ENTRYPOINT = (ROOT / "deploy.sh").read_text(encoding="utf-8")
PREFLIGHT = (ROOT / "scripts" / "preflight_install_state.sh").read_text(
    encoding="utf-8"
)
UNINSTALL_ENTRYPOINT = (ROOT / "scripts" / "uninstall_entrypoint.sh").read_text(
    encoding="utf-8"
)
SETTINGS = (ROOT / "config" / "settings.py").read_text(encoding="utf-8")


class InstallStatePreflightTests(unittest.TestCase):
    def test_official_update_and_deploy_run_preflight_first(self):
        update_case = ENTRYPOINT[ENTRYPOINT.index("        update)") :]
        update_case = update_case[: update_case.index("            ;;")]
        self.assertLess(
            update_case.index("preflight_deploy_state"),
            update_case.index('run_script update_from_github.sh'),
        )

        deploy_case = ENTRYPOINT[ENTRYPOINT.index("        deploy)") :]
        deploy_case = deploy_case[: deploy_case.index("            ;;")]
        self.assertLess(
            deploy_case.index("preflight_deploy_state"),
            deploy_case.index('run_script deploy.sh'),
        )

    def test_fresh_install_is_not_forced_to_have_installed_dependencies(self):
        main = PREFLIGHT[PREFLIGHT.index("main() {") :]
        self.assertLess(
            main.index("признаков предыдущей установки нет"),
            main.index("pg_lsclusters"),
        )
        self.assertIn("--check|--dry-run", main)

    def test_incomplete_install_repairs_only_required_operational_state(self):
        for marker in (
            "обнаружена незавершённая установка",
            "validate_database_revision",
            "install_recovery_backup_tooling",
            "EnvironmentFile=${BACKUP_CONF}",
            "Requires=${PG_UNIT}",
            "обязательный backup выполнит основной transactional deploy",
        ):
            self.assertIn(marker, PREFLIGHT)

    def test_complete_install_fails_closed_when_backup_contract_is_broken(self):
        for marker in (
            "validate_complete_install",
            '"$UNIT_FILE" "$BACKUP_SERVICE" "$BACKUP_SCRIPT" "$BACKUP_CONF"',
            "установленный backup script не является исполняемым",
            "systemd не видит installed backup service",
        ):
            self.assertIn(marker, PREFLIGHT)

    def test_permissions_and_protected_home_are_validated(self):
        for marker in (
            'BOT_HOME=/home/just1kbot',
            'chown root:"$BOT_USER" "$PROJECT_DIR" "$ENV_FILE"',
            'chmod 0750 "$PROJECT_DIR"',
            'chmod 0640 "$ENV_FILE"',
            'runuser -u "$BOT_USER" -- test -r "$ENV_FILE"',
            "--property=ProtectHome=true",
            '/usr/bin/env HOME="$RUNTIME_DIR"',
            "runtime HOME не работает внутри ProtectHome=true sandbox",
        ):
            self.assertIn(marker, PREFLIGHT)

        self.assertIn('_SERVICE_RUNTIME_HOME = "/run/just1kbot"', SETTINGS)
        self.assertIn('os.environ["HOME"] = _SERVICE_RUNTIME_HOME', SETTINGS)

    def test_official_uninstall_removes_orphan_home_only_after_user_is_gone(self):
        self.assertIn("run_script uninstall_entrypoint.sh", ENTRYPOINT)
        self.assertIn('id "$BOT_USER" >/dev/null 2>&1 && return 0', UNINSTALL_ENTRYPOINT)
        self.assertIn('rm -rf --one-file-system -- "$BOT_HOME"', UNINSTALL_ENTRYPOINT)
        self.assertIn("service home остался после purge", UNINSTALL_ENTRYPOINT)


if __name__ == "__main__":
    unittest.main()
