"""Hard architectural guards for the canonical bot.texts layout."""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

from bot import texts

ROOT = Path(__file__).resolve().parent.parent
TEXTS_DIR = ROOT / "bot" / "texts"

EXPECTED_FILES = {
    "__init__.py", "common/__init__.py", "common/buttons.py", "common/errors.py", "common/status.py",
    "user/__init__.py", "user/hub.py", "user/onboarding.py", "user/referral.py", "user/support.py",
    "connection/__init__.py", "connection/devices.py", "connection/config.py", "connection/actions.py",
    "payment/__init__.py", "payment/balance.py", "payment/topup.py", "payment/tariffs.py",
    "payment/tariff_change.py", "payment/status.py",
    "admin/__init__.py", "admin/dashboard.py", "admin/servers.py", "admin/users.py", "admin/tariffs.py",
    "admin/subscriptions.py", "admin/finances.py", "admin/disputes.py", "admin/queues.py", "admin/broadcast.py", "admin/common.py",
    "runtime/__init__.py", "runtime/alerts.py", "runtime/notifications.py",
}

PRODUCTION_DIRS = (ROOT / "bot", ROOT / "services", ROOT / "integrations")


def _collect_docstring_node_ids(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            value = node.body[0].value if isinstance(node.body[0], ast.Expr) else None
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                ids.add(id(value))
    return ids


def _find_cyrillic_literals(path: Path, docstring_node_ids: set[int]) -> list[str]:
    violations: list[str] = []

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.call_stack: list[ast.Call] = []

        def visit_Call(self, node: ast.Call) -> None:
            self.call_stack.append(node)
            self.generic_visit(node)
            self.call_stack.pop()

        def visit_Constant(self, node: ast.Constant) -> None:
            value = node.value
            if (
                isinstance(value, str)
                and any("\u0400" <= char <= "\u04ff" for char in value)
                and id(node) not in docstring_node_ids
                and not self._inside_logger_call()
            ):
                violations.append(f"{path.relative_to(ROOT)}:{node.lineno}: {value[:80]!r}")
            self.generic_visit(node)

        def _inside_logger_call(self) -> bool:
            return any(
                isinstance(call.func, ast.Attribute)
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id in {"logger", "logging", "log", "root_logger"}
                for call in self.call_stack
            )

    Visitor().visit(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
    return violations


class TestTextsLayout(unittest.TestCase):
    def test_exact_domain_layout(self):
        actual = {path.relative_to(TEXTS_DIR).as_posix() for path in TEXTS_DIR.rglob("*.py")}
        self.assertEqual(
            actual,
            EXPECTED_FILES,
            "bot/texts layout drift detected: "
            f"missing={sorted(EXPECTED_FILES - actual)}, unexpected={sorted(actual - EXPECTED_FILES)}",
        )

    def test_facade_is_thin_and_contains_no_text_assignments(self):
        tree = ast.parse((TEXTS_DIR / "__init__.py").read_text(encoding="utf-8"))
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
        self.assertLessEqual(assigned_names, {"_TEXT_KEYS"})

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
                            f"Canonical key {target.id} from {py_file.relative_to(ROOT)} is not exposed by bot.texts",
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
        self.assertEqual(ui_values, [])

    def test_no_cyrillic_string_literals_outside_texts(self):
        violations: list[str] = []
        for base_dir in PRODUCTION_DIRS:
            for path in base_dir.rglob("*.py"):
                if TEXTS_DIR in path.parents:
                    continue
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                violations.extend(_find_cyrillic_literals(path, _collect_docstring_node_ids(tree)))
        self.assertEqual(
            violations,
            [],
            "Cyrillic string literals outside bot/texts detected:\n" + "\n".join(violations),
        )
