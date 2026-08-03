import pathlib
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
PLATFORM = SCRIPTS / "lib" / "install_safe_platform.sh"
DISPATCH = SCRIPTS / "lib" / "install_safe_dispatch.sh"
UNINSTALL_ENTRY = SCRIPTS / "uninstall_foundation.sh"
UNINSTALL_CORE = SCRIPTS / "lib" / "uninstall_safe_core.sh"
UNINSTALL_ACTIONS = SCRIPTS / "lib" / "uninstall_safe_actions.sh"


class ShellHotfixRegressionTests(unittest.TestCase):
    def test_virtualenv_symlink_modes_do_not_fail_writable_path_scan(self):
        with tempfile.TemporaryDirectory() as directory:
            project = pathlib.Path(directory) / "project"
            venv = project / "venv"
            subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)

            subprocess.run(
                [
                    "bash",
                    "-c",
                    "find \"$1\" -xdev -type d -exec chmod 0750 {} +; "
                    "find \"$1\" -xdev -type f -perm /111 -exec chmod 0750 {} +; "
                    "find \"$1\" -xdev -type f ! -perm /111 -exec chmod 0640 {} +; "
                    "! find \"$1\" -xdev \\( -type f -o -type d \\) "
                    "-perm /022 -print -quit | grep -q .",
                    "bash",
                    str(project),
                ],
                check=True,
            )

            symlinks = list(venv.rglob("*"))
            self.assertTrue(any(path.is_symlink() for path in symlinks))

    def test_safe_installer_has_database_key_and_symlink_guards(self):
        platform = PLATFORM.read_text(encoding="utf-8")
        dispatch = DISPATCH.read_text(encoding="utf-8")
        combined = platform + dispatch
        self.assertIn("preflight_postgres_names_absent", combined)
        self.assertIn("DB_ENCRYPTION_KEY", combined)
        self.assertIn("Symlink вне virtualenv запрещён", platform)
        self.assertIn('find "$PROJECT_DIR" -xdev -type l', platform)
        self.assertIn("foundation_preflight_static_resources", dispatch)
        self.assertIn("preflight_before_packages", dispatch)

    def test_manifest_preflight_precedes_any_uninstall_stop(self):
        entry = UNINSTALL_ENTRY.read_text(encoding="utf-8")
        core = UNINSTALL_CORE.read_text(encoding="utf-8")
        actions = UNINSTALL_ACTIONS.read_text(encoding="utf-8")
        main = entry[entry.index("main()") :]
        self.assertLess(main.index("manifest_preflight"), main.index("stop_units"))
        self.assertLess(main.index("prepare_postgres"), main.index("stop_units"))
        self.assertLess(main.index("backup_before_keep"), main.index("stop_units"))
        self.assertLess(main.index("stop_units"), main.index("remove_files"))
        for source in (entry, core, actions):
            self.assertNotIn("ufw ", source)
            self.assertNotIn("setup-amnezia-api.sh", source)
            self.assertNotIn("/etc/redis/redis.conf", source)

    def test_legacy_wrappers_cannot_bypass_safe_control_plane(self):
        deploy_wrapper = (SCRIPTS / "deploy.sh").read_text(encoding="utf-8")
        uninstall_wrapper = (SCRIPTS / "uninstall.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn('exec /bin/bash "$CONTROL" deploy "$@"', deploy_wrapper)
        self.assertIn('exec /bin/bash "$TARGET" "$@"', uninstall_wrapper)
        for source in (deploy_wrapper, uninstall_wrapper):
            self.assertNotIn("apt-get", source)
            self.assertNotIn("systemctl stop", source)
            self.assertNotIn("rm -rf", source)


if __name__ == "__main__":
    unittest.main()
