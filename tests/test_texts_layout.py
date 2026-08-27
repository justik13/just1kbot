"""Hard architectural guards for the canonical bot.texts layout."""
from __future__ import annotations

import ast
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


class TextsLayoutTests:
    def test_exact_domain_layout(self):
        actual = {
            path.relative_to(TEXTS_DIR).as_posix()
            for path in TEXTS_DIR.rglob("*.py")
        }
        assert actual == EXPECTED_FILES, (
            "bot/texts layout drift detected. "
            f"Missing={sorted(EXPECTED_FILES - actual)}, "
            f"Unexpected={sorted(actual - EXPECTED_FILES)}"
        )

    def test_facade_is_thin_and_contains_no_text_assignments(self):
        tree = ast.parse(
            (TEXTS_DIR / "__init__.py").read_text(encoding="utf-8"),
            filename="bot/texts/__init__.py",
        )
        assignments = [
            node
            for node in tree.body
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr))
        ]
        assigned_names = set()
        for node in assignments:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    assigned_names.add(target.id)
        assert assigned_names <= {"_TEXT_KEYS"}, (
            "Facade must not own canonical text constants: "
            f"{sorted(assigned_names - {'_TEXT_KEYS'})}"
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
                        assert hasattr(texts, target.id), (
                            f"Canonical key {target.id} from {py_file.relative_to(ROOT)} "
                            "is not exposed by bot.texts"
                        )
