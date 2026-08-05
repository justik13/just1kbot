import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ROOT_LOADER = ROOT / "deploy.sh"
CONTROL_MODULE = ROOT / "scripts" / "lib" / "control_plane.sh"
STATE_INSPECTOR = ROOT / "scripts" / "inspect_install_state.sh"
DIAGNOSTICS = ROOT / "scripts" / "lib" / "installer_diagnostics.sh"
ACTIVATION_POLICY = ROOT / "scripts" / "lib" / "install_safe_activation_policy.sh"


class InstallerControlPlaneContractTests(unittest.TestCase):
    def test_shell_scripts_parse(self):
        for script in (
            ROOT_LOADER,
            CONTROL_MODULE,
            STATE_INSPECTOR,
            DIAGNOSTICS,
            ACTIVATION_POLICY,
        ):
            with self.subTest(script=script):
                result = subprocess.run(
                    ["bash", "-n", str(script)],
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_root_loader_is_thin_and_loads_safe_module(self):
        loader = ROOT_LOADER.read_text(encoding="utf-8")
        self.assertIn('module="$SCRIPTS_DIR/lib/control_plane.sh"', loader)
        self.assertIn('source "$module"', loader)
        self.assertIn('dispatch "$@"', loader)
        self.assertNotIn("setup-amnezia-api.sh", loader)
        self.assertNotIn("apt-get", loader)
        self.assertNotIn("systemctl", loader)

    def test_amnezia_setup_is_not_reachable_from_control_plane(self):
        source = CONTROL_MODULE.read_text(encoding="utf-8")
        self.assertNotIn("setup-amnezia-api.sh", source.split("Standalone", 1)[0])
        self.assertNotIn("dispatch amnezia", source)
        self.assertNotIn("amnezia)", source)
        self.assertNotIn("sudo bash deploy.sh amnezia", source)
        self.assertIn("Standalone setup-amnezia-api.sh", source)

    def test_preflight_runs_collision_inspector_before_state_preflight(self):
        source = CONTROL_MODULE.read_text(encoding="utf-8")
        function = source[source.index("preflight()") : source.index("smoke()")]
        self.assertLess(
            function.index("state --operation deploy --require-safe"),
            function.index("preflight_install_state.sh"),
        )

    def test_control_plane_exposes_recovery_and_manifest_uninstall(self):
        source = CONTROL_MODULE.read_text(encoding="utf-8")
        for marker in (
            "install-recover",
            "install-rollback",
            "uninstall --keep-data|--purge-data",
            "uninstall_entrypoint.sh",
            "state [--json]",
            "update [--sha COMMIT]",
        ):
            self.assertIn(marker, source)

    def test_recovery_bootstrap_is_installed_before_mutations_and_cleaned(self):
        source = ACTIVATION_POLICY.read_text(encoding="utf-8")
        self.assertIn("stage_recovery_bundle", source)
        self.assertIn("install_recovery_cli_launcher", source)
        self.assertIn("PRIMARY=/opt/just1kbot/deploy.sh", source)
        self.assertIn("remove_recovery_bootstrap", source)
        self.assertIn("remove_recovery_path", source)
        self.assertIn("recovery_paths_safe_for_cleanup", source)
        self.assertIn("CLI_BOOTSTRAP_MARKER", source)
        self.assertIn("root:root 750", source)
        self.assertIn("rollback_empty_pre_manifest_journal", source)
        self.assertIn("remove_recovery_bundle", source)

        transaction = source[
            source.index("begin_installer_transaction()") : source.index("activate_release_bundle()")
        ]
        self.assertLess(
            transaction.index("foundation_journal_begin \"$operation\" preflight"),
            transaction.index("stage_recovery_bundle"),
        )

        activate = source[source.index("activate_release_bundle()"):]
        self.assertLess(
            activate.index("foundation_install_cli"),
            activate.index("install_recovery_cli_launcher"),
        )

        automatic_rollback = source[
            source.index("automatic_initial_rollback()") : source.index("base_run_deploy_definition")
        ]
        self.assertLess(
            automatic_rollback.index("recovery_paths_safe_for_cleanup"),
            automatic_rollback.index("base_automatic_initial_rollback"),
        )

        rollback = source[
            source.index("rollback_incomplete()") : source.index("if [[ \"${INSTALL_SAFE_ACTIVATION_POLICY_SOURCE_ONLY:-0}\"",)
        ]
        self.assertLess(
            rollback.index("recovery_paths_safe_for_cleanup"),
            rollback.index('bash "$SCRIPT_DIR/uninstall_foundation.sh"'),
        )

        pre_manifest = source[
            source.index("rollback_empty_pre_manifest_journal()") : source.index("base_automatic_initial_rollback_definition")
        ]
        self.assertIn('"path:$CLI_PATH"', pre_manifest)
        self.assertLess(
            pre_manifest.index("recovery_paths_safe_for_cleanup"),
            pre_manifest.index("remove_recovery_bootstrap"),
        )

    def test_diagnostics_explain_problem_and_next_action(self):
        source = DIAGNOSTICS.read_text(encoding="utf-8")
        for required in (
            "ОШИБКА JUST1KBOT",
            "Операция:",
            "Этап:",
            "Проблема:",
            "Причина:",
            "Что сделать:",
            "Команды диагностики:",
        ):
            self.assertIn(required, source)

    def test_state_inspector_is_read_only_and_fail_closed(self):
        source = STATE_INSPECTOR.read_text(encoding="utf-8")
        for forbidden in ("rm", "chown", "chmod", "useradd", "systemctl start"):
            with self.subTest(forbidden=forbidden):
                self.assertFalse(
                    any(
                        line.lstrip().startswith(forbidden + " ")
                        for line in source.splitlines()
                    ),
                    f"state inspector must be read-only: {forbidden}",
                )
        for state in (
            "clean",
            "installed_managed",
            "legacy_managed",
            "partial_install",
            "residual_managed",
            "foreign_collision",
            "corrupted_state",
        ):
            self.assertIn(state, source)
        self.assertIn("--require-safe", source)
        self.assertIn("Installer не выполнит rm, chown, chmod", source)


if __name__ == "__main__":
    unittest.main()
