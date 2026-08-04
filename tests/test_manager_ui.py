import pathlib
import subprocess
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
UI = ROOT / "scripts" / "lib" / "control_plane_ui.sh"
DEPLOY = ROOT / "deploy.sh"


class ManagerUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ui = UI.read_text(encoding="utf-8")
        cls.deploy = DEPLOY.read_text(encoding="utf-8")

    def test_ui_module_parses(self):
        result = subprocess.run(
            ["bash", "-n", str(UI)],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_ui_is_loaded_after_final_routing(self):
        self.assertIn("control_plane_ui.sh", self.deploy)
        self.assertLess(
            self.deploy.index("control_plane_final.sh"),
            self.deploy.index("control_plane_ui.sh"),
        )
        self.assertIn('require_safe_script "$ui"', self.deploy)

    def test_every_real_state_has_a_menu(self):
        for state in (
            "clean",
            "installed_managed",
            "partial_install",
            "legacy_managed|residual_managed",
            "foreign_collision|corrupted_state|unknown",
        ):
            self.assertIn(state, self.ui)

    def test_blocked_state_hides_mutating_actions(self):
        blocked = self.ui[
            self.ui.index("ui_blocked_menu()") : self.ui.index("\nmenu() {")
        ]
        self.assertIn("Mutating actions скрыты", blocked)
        self.assertNotIn("dispatch deploy", blocked)
        self.assertNotIn("dispatch uninstall", blocked)
        self.assertNotIn("dispatch repair --apply", blocked)

    def test_update_is_manual_and_exact_sha_bound(self):
        update = self.ui[
            self.ui.index("ui_update_menu()") : self.ui.index(
                "ui_service_status_menu()"
            )
        ]
        self.assertIn("dispatch update --check", update)
        self.assertIn("^[0-9a-f]{40}$", update)
        self.assertIn('update --sha "$fetched"', update)
        self.assertNotIn("--yes", update)

    def test_destructive_actions_have_explicit_confirmation(self):
        self.assertIn("DELETE BACKUPS", self.ui)
        self.assertIn("Введите STOP", self.ui)
        self.assertIn("DELETE JUST1KBOT", self.ui)

    def test_live_logs_support_q_exit(self):
        self.assertIn("read -r -s -n 1 -t 1 key", self.ui)
        self.assertIn('[[ "$key" == q || "$key" == Q ]]', self.ui)
        self.assertIn("tail -n 50 -F", self.ui)
        self.assertIn("journalctl -u \"$UI_SERVICE\" -n 50 -f", self.ui)

    def test_russian_primary_navigation_is_present(self):
        for marker in (
            "JUST1KBOT MANAGER",
            "Проверить обновления",
            "Бэкапы и восстановление",
            "Диагностика",
            "Статус сервиса",
            "Удаление",
        ):
            self.assertIn(marker, self.ui)


if __name__ == "__main__":
    unittest.main()
