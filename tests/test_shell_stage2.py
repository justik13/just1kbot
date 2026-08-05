import pathlib
import subprocess
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
CONTROL = SCRIPTS / "lib" / "control_plane.sh"


class ShellStage2Tests(unittest.TestCase):
    def test_required_runtime_files_exist(self):
        required = [
            "install_safe.sh",
            "update_from_github.sh",
            "update_from_github_complete.sh",
            "uninstall_foundation.sh",
            "uninstall_entrypoint.sh",
            "inspect_install_state.sh",
            "preflight_install_state.sh",
            "ops/deploy_application.sh",
            "ops/backup_postgres.sh",
            "ops/verify_backup.sh",
            "ops/restore_rehearsal.sh",
            "ops/just1kbot-restore.sh",
            "ops/production_restore.sh",
            "ops/doctor.sh",
            "ops/doctor_complete.sh",
            "ops/doctor_json.sh",
            "ops/repair.sh",
            "ops/repair_complete.sh",
            "ops/support_bundle.sh",
            "preflight.sh",
            "lib/control_plane.sh",
            "lib/control_plane_completion.sh",
            "lib/control_plane_final.sh",
            "lib/installer_foundation.sh",
            "lib/installer_foundation_compat.sh",
            "lib/install_safe_platform.sh",
            "lib/install_safe_release_contract.sh",
            "lib/install_safe_lock_policy.sh",
            "lib/install_safe_runtime.sh",
            "lib/install_safe_tls_policy.sh",
            "lib/install_safe_postgres_ownership.sh",
            "lib/install_safe_proxy_mode.sh",
            "lib/install_safe_activation_policy.sh",
            "lib/install_safe_failure_injection.sh",
            "lib/install_safe_dispatch.sh",
            "lib/uninstall_safe_core.sh",
            "lib/uninstall_safe_actions.sh",
            "lib/uninstall_safe_ownership.sh",
            "lib/production_restore_core.sh",
            "lib/production_restore_runtime.sh",
            "lib/production_restore_actions.sh",
            "lib/production_restore_input.sh",
            "lib/production_restore_crash.sh",
            "lib/production_restore_recovery_cleanup.sh",
            "setup-amnezia-api.sh",
        ]
        self.assertEqual(
            [path for path in required if not (SCRIPTS / path).is_file()],
            [],
        )

    def test_new_scripts_parse(self):
        scripts = [
            SCRIPTS / path
            for path in (
                "install_safe.sh",
                "update_from_github.sh",
                "update_from_github_complete.sh",
                "uninstall_foundation.sh",
                "uninstall_entrypoint.sh",
                "inspect_install_state.sh",
                "preflight_install_state.sh",
                "lib/control_plane.sh",
                "lib/control_plane_completion.sh",
                "lib/control_plane_final.sh",
                "lib/installer_foundation.sh",
                "lib/installer_foundation_compat.sh",
                "lib/install_safe_platform.sh",
                "lib/install_safe_release_contract.sh",
                "lib/install_safe_lock_policy.sh",
                "lib/install_safe_runtime.sh",
                "lib/install_safe_tls_policy.sh",
                "lib/install_safe_postgres_ownership.sh",
                "lib/install_safe_proxy_mode.sh",
                "lib/install_safe_activation_policy.sh",
                "lib/install_safe_failure_injection.sh",
                "lib/install_safe_dispatch.sh",
                "lib/uninstall_safe_core.sh",
                "lib/uninstall_safe_actions.sh",
                "lib/uninstall_safe_ownership.sh",
                "ops/doctor.sh",
                "ops/doctor_complete.sh",
                "ops/doctor_json.sh",
                "ops/repair.sh",
                "ops/repair_complete.sh",
                "ops/support_bundle.sh",
                "preflight.sh",
            )
        ]
        for script in scripts:
            subprocess.run(["bash", "-n", str(script)], check=True)

    def test_manifest_uninstall_is_fail_closed_and_interactive(self):
        entry = (SCRIPTS / "uninstall_foundation.sh").read_text(encoding="utf-8")
        self.assertIn("confirm", entry)
        self.assertIn("read_env", entry)

        core = (SCRIPTS / "lib" / "uninstall_safe_core.sh").read_text(encoding="utf-8")
        actions = (SCRIPTS / "lib" / "uninstall_safe_actions.sh").read_text(encoding="utf-8")
        ownership = (SCRIPTS / "lib" / "uninstall_safe_ownership.sh").read_text(encoding="utf-8")

        for forbidden in ("rm -rf /", "userdel -f", "dropdb --if-exists"):
            self.assertNotIn(forbidden, core)
            self.assertNotIn(forbidden, actions)
            self.assertNotIn(forbidden, ownership)

    def test_rehearsal_requires_interactive_confirmation_or_force_flag(self):
        source = (SCRIPTS / "ops" / "restore_rehearsal.sh").read_text(encoding="utf-8")
        self.assertIn("rehearsal_confirm", source)
        self.assertIn("--force", source)

    def test_standalone_setup_script_exists_and_uses_safe_wrappers(self):
        path = SCRIPTS / "setup-amnezia-api.sh"
        self.assertTrue(path.is_file())
        text = path.read_text(encoding="utf-8")
        self.assertIn("set -Eeuo pipefail", text)
        self.assertIn("umask 077", text)
        self.assertNotIn("curl | bash", text)
        self.assertNotIn("wget | sh", text)

    def test_control_plane_help_includes_legacy_aliases(self):
        source = CONTROL.read_text(encoding="utf-8")
        self.assertIn("setup-amnezia-api.sh", source.split("Standalone", 1)[0])
        self.assertIn("Standalone setup-amnezia-api.sh", source)
        self.assertIn("uninstall_entrypoint.sh", source)
        self.assertIn("ops/just1kbot-restore.sh rehearsal", source)
        self.assertIn("ops/just1kbot-restore.sh production", source)


if __name__ == "__main__":
    unittest.main()
