"""Guard new Alembic migrations against f-string SQL interpolation.

PostgreSQL DDL (DROP INDEX/TABLE, CREATE INDEX CONCURRENTLY) accepts no
bind parameters, so identifiers historically leaked into f-strings. The
five files in LEGACY_ALLOWLIST are frozen history and must not be
rewritten; every NEW migration must validate identifiers against a
strict whitelist (e.g. ``^[a-z_][a-z0-9_]*$``) instead of interpolating
unchecked values into ``sa.text()`` / ``op.execute()``.
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSIONS_DIR = ROOT / "alembic" / "versions"

# Frozen history: f-string DDL sites that predate this guard. Do not extend.
# New migrations with the same pattern fail the test below.
LEGACY_ALLOWLIST = frozenset(
    {
        "0006_add_user_subscription_token.py",
        "0009_payments_referral_bonus_idx.py",
        "0012_payment_debits_and_audit_target_idx.py",
        "0015_payments_auto_fulfill_retry_idx.py",
        "0032_drop_banking_residue.py",
    }
)

_SQL_CALLS = frozenset({"text", "execute"})


def _fstring_sql_lines(path: Path) -> list[int]:
    """Return lines where an f-string reaches sa.text()/text()/op.execute()."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            name = func.attr
        elif isinstance(func, ast.Name):
            name = func.id
        else:
            continue
        if name not in _SQL_CALLS:
            continue
        args = list(node.args) + [kw.value for kw in node.keywords]
        if any(isinstance(sub, ast.JoinedStr) for arg in args for sub in ast.walk(arg)):
            lines.append(node.lineno)
    return sorted(set(lines))


class TestMigrationSqlHygiene(unittest.TestCase):
    def test_no_new_fstring_sql_in_migrations(self):
        violations = []
        for path in sorted(VERSIONS_DIR.glob("*.py")):
            if path.name in LEGACY_ALLOWLIST:
                continue
            lines = _fstring_sql_lines(path)
            if lines:
                violations.append(f"{path.name}:{','.join(map(str, lines))}")
        self.assertEqual(
            violations,
            [],
            "New f-string SQL in migrations (use identifier whitelist instead):\n"
            + "\n".join(violations),
        )

    def test_legacy_allowlist_is_accurate(self):
        for name in sorted(LEGACY_ALLOWLIST):
            path = VERSIONS_DIR / name
            self.assertTrue(path.is_file(), f"allowlisted migration removed: {name}")
            self.assertTrue(
                _fstring_sql_lines(path),
                f"allowlisted migration no longer needs the exemption: {name}",
            )
