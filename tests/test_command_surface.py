import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]


class CommandSurfaceTests(unittest.TestCase):
    def test_only_start_is_registered_as_bot_command(self):
        source = (ROOT / "bot" / "main.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        commands = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Name) and func.id == "BotCommand"):
                continue
            command = next(
                (item.value for item in node.keywords if item.arg == "command"),
                None,
            )
            if isinstance(command, ast.Constant):
                commands.append(command.value)
        self.assertEqual(commands, ["start"])

    def test_only_command_start_filter_exists(self):
        handlers = ROOT / "bot" / "handlers"
        command_filters = []
        for path in handlers.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = node.func.id if isinstance(node.func, ast.Name) else None
                if name in {"Command", "CommandStart"}:
                    command_filters.append((path.relative_to(ROOT).as_posix(), name))
        self.assertEqual(command_filters, [("bot/handlers/start.py", "CommandStart")])

    def test_fallback_is_last_and_returns_to_button_menu(self):
        main = (ROOT / "bot" / "main.py").read_text(encoding="utf-8")
        fallback = (ROOT / "bot" / "handlers" / "fallback.py").read_text(
            encoding="utf-8"
        )
        self.assertLess(main.index("admin_router,"), main.index("fallback_router,"))
        self.assertIn("message.delete()", fallback)
        self.assertIn("@router.message()", fallback)

    def test_admin_navigation_has_no_slash_entrypoint(self):
        admin_root = ROOT / "bot" / "handlers" / "admin"
        for path in admin_root.rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("Command(", source, path.relative_to(ROOT).as_posix())
            self.assertNotIn("CommandStart(", source, path.relative_to(ROOT).as_posix())


if __name__ == "__main__":
    unittest.main()
