"""Hard architectural guards for the canonical bot.texts layout."""
from __future__ import annotations

import ast
import re
import string
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

PRODUCTION_DIRS = (ROOT / "bot", ROOT / "services", ROOT / "utils", ROOT / "integrations")


def _collect_docstring_node_ids(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            value = node.body[0].value if isinstance(node.body[0], ast.Expr) else None
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                ids.add(id(value))
    return ids


def _find_cyrillic_literals(path: Path, tree: ast.AST, docstring_node_ids: set[int]) -> list[str]:
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

    Visitor().visit(tree)
    return violations


def _canonical_text_fields() -> dict[str, set[str]]:
    fields: dict[str, set[str]] = {}
    formatter = string.Formatter()
    for py_file in TEXTS_DIR.rglob("*.py"):
        if py_file.name == "__init__.py":
            continue
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for statement in tree.body:
            if not isinstance(statement, ast.Assign) or not isinstance(statement.value, ast.Constant):
                continue
            if not isinstance(statement.value.value, str):
                continue
            for target in statement.targets:
                if not isinstance(target, ast.Name) or not target.id.isupper():
                    continue
                names: set[str] = set()
                for _, field_name, _, _ in formatter.parse(statement.value.value):
                    if field_name and not field_name.isdigit() and not field_name.startswith("{"):
                        names.add(field_name.split(".", 1)[0].split("[", 1)[0])
                fields[target.id] = names
    return fields


def _find_missing_text_format_args() -> list[str]:
    expected = _canonical_text_fields()
    violations: list[str] = []

    class Visitor(ast.NodeVisitor):
        def __init__(self, path: Path) -> None:
            self.path = path

        def visit_Call(self, node: ast.Call) -> None:
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "format":
                key = None
                call_repr = None
                if (
                    isinstance(func.value, ast.Attribute)
                    and isinstance(func.value.value, ast.Name)
                    and func.value.value.id == "texts"
                ):
                    key = func.value.attr
                    call_repr = f"texts.{key}.format()"
                elif isinstance(func.value, ast.Name) and func.value.id in expected:
                    key = func.value.id
                    call_repr = f"{key}.format()"

                if key is not None:
                    required = expected.get(key, set())
                    if required:
                        provided = {kw.arg for kw in node.keywords if kw.arg is not None}
                        has_dynamic_kwargs = any(kw.arg is None for kw in node.keywords)
                        has_pos_args = len(node.args) > 0
                        if not has_dynamic_kwargs and not has_pos_args:
                            missing = sorted(required - provided)
                            if missing:
                                violations.append(
                                    f"{self.path.relative_to(ROOT)}:{node.lineno}: {call_repr} missing {missing}"
                                )
            self.generic_visit(node)

    for base_dir in PRODUCTION_DIRS:
        for path in base_dir.rglob("*.py"):
            if TEXTS_DIR in path.parents:
                continue
            if "integrations" in path.parts:
                content = path.read_text(encoding="utf-8")
                if "aiogram" not in content:
                    continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except SyntaxError as exc:
                violations.append(f"{path.relative_to(ROOT)}:{exc.lineno}: syntax error: {exc.msg}")
                continue
            Visitor(path).visit(tree)
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
        """Константы конфига не должны быть UI-текстами.

        Технические идентификаторы (protocol='amneziawg2', таймауты-строки и
        т.п.) допустимы; запрещены значения с кириллицей и англоязычные фразы.
        """
        path = ROOT / "config" / "constants.py"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        ui_values = []
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.isupper():
                    if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                        value = node.value.value
                        if any("\u0400" <= ch <= "\u04ff" for ch in value):
                            ui_values.append((target.id, value))
                            continue
                        words = [w for w in re.split(r"\s+", value.strip()) if w]
                        if len(words) >= 2 and all(re.search(r"[A-Za-z]{3}", w) for w in words):
                            ui_values.append((target.id, value))
        self.assertEqual(ui_values, [])

    def test_no_cyrillic_string_literals_outside_texts(self):
        violations: list[str] = []
        for base_dir in PRODUCTION_DIRS:
            for path in base_dir.rglob("*.py"):
                if TEXTS_DIR in path.parents:
                    continue
                if "integrations" in path.parts:
                    content = path.read_text(encoding="utf-8")
                    if "aiogram" not in content:
                        continue
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                violations.extend(
                    _find_cyrillic_literals(path, tree, _collect_docstring_node_ids(tree))
                )
        self.assertEqual(
            violations,
            [],
            "Cyrillic string literals outside bot/texts detected:\n" + "\n".join(violations),
        )

    def test_text_format_calls_provide_all_named_placeholders(self):
        violations = _find_missing_text_format_args()
        self.assertEqual(
            violations,
            [],
            "Text format calls have missing placeholders:\n" + "\n".join(violations),
        )
