import pathlib
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


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

    def test_deploy_has_database_key_and_symlink_guards(self):
        text = (SCRIPTS / "deploy.sh").read_text(encoding="utf-8")
        self.assertIn("preflight_initial_database_without_env", text)
        self.assertIn("DB_ENCRYPTION_KEY; новый ключ создавать запрещено", text)
        self.assertIn("validate_live_symlinks", text)
        self.assertIn("Symlink вне virtualenv запрещён", text)
        self.assertIn("readlink -f", text)

    def test_uninstall_preflight_precedes_any_operational_stop(self):
        text = (SCRIPTS / "uninstall.sh").read_text(encoding="utf-8")
        main = text[text.index("main() {") :]
        self.assertLess(main.index("run_resource_preflight"), main.index("preflight_purge"))
        self.assertLess(main.index("preflight_purge"), main.index("pause_operational_work"))
        self.assertLess(main.index("pause_operational_work"), main.index("stop_units"))
        self.assertNotIn("mapfile -t connection < <(redis_connection)", text)
        self.assertNotIn("ufw ", text)
        self.assertNotIn("setup-amnezia-api.sh", text)


if __name__ == "__main__":
    unittest.main()
