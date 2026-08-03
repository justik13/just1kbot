import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTROL_PLANE = ROOT / "deploy.sh"
STATE_INSPECTOR = ROOT / "scripts" / "inspect_install_state.sh"
DIAGNOSTICS = ROOT / "scripts" / "lib" / "installer_diagnostics.sh"


class InstallerControlPlaneContractTests(unittest.TestCase):
    def test_shell_scripts_parse(self):
        for script in (CONTROL_PLANE, STATE_INSPECTOR, DIAGNOSTICS):
            with self.subTest(script=script):
                result = subprocess.run(
                    ["bash", "-n", str(script)],
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_amnezia_setup_is_not_reachable_from_control_plane(self):
        source = CONTROL_PLANE.read_text(encoding="utf-8")
        self.assertNotIn("run_script setup-amnezia-api.sh", source)
        self.assertNotIn("dispatch amnezia", source)
        self.assertNotIn("amnezia)", source)
        self.assertNotIn("sudo bash deploy.sh amnezia", source)
        self.assertIn("Standalone setup-amnezia-api.sh", source)

    def test_preflight_runs_collision_inspector_before_mutating_preflight(self):
        source = CONTROL_PLANE.read_text(encoding="utf-8")
        function = source.split("preflight_deploy_state()", 1)[1].split("\n}\n", 1)[0]
        self.assertLess(
            function.index("inspect_deploy_state --require-safe"),
            function.index("preflight_install_state.sh"),
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
                    any(line.lstrip().startswith(forbidden + " ") for line in source.splitlines()),
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
