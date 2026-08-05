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
            )
        ]
        for script in scripts:
            subprocess.run(["bash", "-n", str(script)], check=True)

    def test_manifest_uninstall_is_fail_closed_and_interactive(self):
        entry = (SCRIPTS / "uninstall_foundation.sh").read_text(encoding="utf-8")
        modules = [
            (SCRIPTS / "lib" / name).read_text(encoding="utf-8")
            for name in (
                "uninstall_safe_core.sh",
                "uninstall_safe_actions.sh",
                "uninstall_safe_ownership.sh",
            )
        ]
        combined = "\n".join((entry, *modules))

        for marker in (
            "foundation_manifest_require",
            "firewall_managed",
            "DELETE JUST1KBOT",
            "backup_before_keep",
            "foundation_journal_begin uninstall",
            "foundation_manifest_has",
            "require_owned_path",
            "remove_owned_file",
            "remove_owned_tree",
            "DROP ROLE IF EXISTS",
            "^just1kbot_(stg|rb|fail)_",
            "postgres_expected_marker",
            "ownership-aware uninstall verification",
            "post_verify",
        ):
            self.assertIn(marker, combined)
        for forbidden in (
            "apt-get remove",
            "ufw ",
            "setup-amnezia-api.sh",
            "docker ",
            "sed -i /etc/redis/redis.conf",
            "rm -f /etc/redis/redis.conf",
            "rm -rf /etc/redis",
            "> /etc/redis/redis.conf",
        ):
            self.assertNotIn(forbidden, combined)

        main = entry[entry.index("main()") :]
        self.assertLess(main.index("manifest_preflight"), main.index("stop_units"))
        self.assertLess(main.index("backup_before_keep"), main.index("stop_units"))
        self.assertLess(main.index("stop_units"), main.index("remove_files"))
        self.assertLess(main.index("remove_files"), main.index("post_verify"))

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
            "OPERATION_LOCK=/run/lock/just1kbot-deploy.lock",
        ):
            self.assertIn(marker, text)
        self.assertNotIn("sed -i '/http {/", text)

    def test_control_plane_excludes_standalone_amnezia_utility(self):
        source = CONTROL.read_text(encoding="utf-8")
        self.assertNotIn("setup-amnezia-api.sh", source.split("Standalone", 1)[0])
        self.assertNotIn("amnezia)", source)
        self.assertIn("Standalone setup-amnezia-api.sh", source)
        self.assertIn("uninstall_entrypoint.sh", source)
        self.assertIn("install-recover", source)
        self.assertIn("install-rollback", source)
        self.assertIn("ops/just1kbot-restore.sh rehearsal", source)
        self.assertIn("ops/just1kbot-restore.sh production", source)
        self.assertIn("state --operation deploy --require-safe", source)


if __name__ == "__main__":
    unittest.main()
