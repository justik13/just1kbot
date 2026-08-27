"""Hard architectural guards for the canonical bot.texts layout."""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

from bot import texts

ROOT = Path(__file__).resolve().parent.parent
TEXTS_DIR = ROOT / "bot" / "texts"

EXPECTED_FILES = {
    "__init__.py",
    "common/__init__.py",
    "common/buttons.py",
    "common/errors.py",
    "common/status.py",
    "user/__init__.py",
    "user/hub.py",
    "user/onboarding.py",
    "user/referral.py",
    "user/support.py",
    "connection/__init__.py",
    "connection/devices.py",
    "connection/config.py",
    "connection/actions.py",
    "payment/__init__.py",
    "payment/balance.py",
    "payment/topup.py",
    "payment/tariffs.py",
    "payment/tariff_change.py",
    "payment/status.py",
    "admin/__init__.py",
    "admin/dashboard.py",
    "admin/servers.py",
    "admin/users.py",
    "admin/tariffs.py",
    "admin/subscriptions.py",
    "admin/finances.py",
    "admin/disputes.py",
    "admin/queues.py",
    "admin/broadcast.py",
    "admin/common.py",
    "runtime/__init__.py",
    "runtime/alerts.py",
    "runtime/notifications.py",
}

PRODUCTION_DIRS = (
    ROOT / "bot",
    ROOT / "services",
    ROOT / "integrations",
)


class TestTextsLayout(unittest.TestCase):
    def test_exact_domain_layout(self):
        actual = {
            path.relative_to(TEXTS_DIR).as_posix()
            for path in TEXTS_DIR.rglob("*.py")
        }
        self.assertEqual(
            actual,
            EXPECTED_FILES,
            "bot/texts layout drift detected: "
            f"missing={sorted(EXPECTED_FILES - actual)}, "
            f"unexpected={sorted(actual - EXPECTED_FILES)}",
        )

    def test_facade_is_thin_and_contains_no_text_assignments(self):
        tree = ast.parse(
            (TEXTS_DIR / "__init__.py").read_text(encoding="utf-8"),
            filename="bot/texts/__init__.py",
        )
        assigned_names = set()
        for node in tree.body:
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
            else:
                continue
            for target in targets:
                if isinstance(target, ast.Name):
                    assigned_names.add(target.id)
        self.assertLessEqual(
            assigned_names,
            {"_TEXT_KEYS"},
            "Facade must not own canonical text constants: "
            f"{sorted(assigned_names - {'_TEXT_KEYS'})}",
        )

    def test_every_canonical_text_is_reachable_through_facade(self):
        for py_file in TEXTS_DIR.rglob("*.py"):
            if py_file.name == "__init__.py":
                continue
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
            for statement in tree.body:
                if not isinstance(statement, ast.Assign):
                    continue
                for target in statement.targets:
                    if isinstance(target, ast.Name) and target.id.isupper():
                        self.assertTrue(
                            hasattr(texts, target.id),
                            f"Canonical key {target.id} from {py_file.relative_to(ROOT)} "
                            "is not exposed by bot.texts",
                        )

    def test_config_constants_has_no_user_facing_text(self):
        path = ROOT / "config" / "constants.py"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        ui_values = []
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.isupper():
                    if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                        ui_values.append((target.id, node.value.value))
        self.assertEqual(
            ui_values,
            [],
            "User-facing string literals must live under bot/texts, not config/constants.py",
        )

    def test_no_cyrillic_string_literals_outside_texts(self):
        """Production modules must not own Russian UI text outside bot/texts."""
        violations = []

        class Visitor(ast.NodeVisitor):
            def __init__(self, path: Path):
                self.path = path
                self.call_stack: list[ast.Call] = []

            def visit_Call(self, node: ast.Call):
                self.call_stack.append(node)
                self.generic_visit(node)
                self.call_stack.pop()

            def visit_Constant(self, node: ast.Constant):
                if not isinstance(node.value, str) or not any("\u0400" <= ch <= "\u04ff" for ch in node.value):
                    self.generic_visit(node)
                    return
                if self._is_docstring(node) or self._is_logging_argument():
                    return
                violations.append(
                    f"{self.path.relative_to(ROOT)}:{node.lineno}: {node.value[:80]!r}"
                )
                self.generic_visit(node)

            def _is_docstring(self, node: ast.Constant) -> bool:
                return isinstance(getattr(node, "parent", None), (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))

            def _is_logging_argument(self) -> bool:
                for call in self.call_stack:
                    if isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name):
                        if call.func.value.id in {"logger", "logging", "log", "root_logger"}:
                            return True
                return False

        for base_dir in PRODUCTION_DIRS:
            for path in base_dir.rglob("*.py"):
                if TEXTS_DIR in path.parents or path == TEXTS_DIR / "__init__.py":
                    continue
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                for node in ast.walk(tree):
                    if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        docstring = ast.get_docstring(node, clean=False)
                        if docstring and node.body and isinstance(node.body[0], ast.Expr):
                            node.body[0].value.parent = node
                for node in ast.walk(tree):
                    if isinstance(node, ast.Constant) and isinstance(node.value, str):
                        for parent_node in ast.walk(tree):
                            if isinstance(parent_node, ast.Expr) and parent_node.value is node:
                                node.parent = parent_node.parent if hasattr(parent_node, "parent") else None
                visitor = Visitor(path)
                visitor.visit(tree)

        self.assertEqual(
            violations,
            [],
            "Cyrillic string literals outside bot/texts detected:\n" + "\n".join(violations),
        )
