"""Ensure the Alembic migration revision graph is strictly linear and valid."""

import ast
from pathlib import Path
import unittest


class AlembicMigrationChainTests(unittest.TestCase):
    def test_migration_chain_is_linear_and_heads_valid(self):
        versions_dir = Path("alembic/versions")
        self.assertTrue(versions_dir.exists(), "alembic/versions directory missing")

        revisions: dict[str, str | None] = {}

        for py_file in versions_dir.glob("*.py"):
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
            rev = None
            down_rev = None
            for stmt in tree.body:
                if isinstance(stmt, ast.Assign):
                    for target in stmt.targets:
                        if isinstance(target, ast.Name):
                            if target.id == "revision" and isinstance(stmt.value, ast.Constant):
                                rev = stmt.value.value
                            elif target.id == "down_revision":
                                if isinstance(stmt.value, ast.Constant):
                                    down_rev = stmt.value.value
                                elif isinstance(stmt.value, ast.Name) and stmt.value.id == "None":
                                    down_rev = None
                elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    if stmt.target.id == "revision" and isinstance(stmt.value, ast.Constant):
                        rev = stmt.value.value
                    elif stmt.target.id == "down_revision":
                        if isinstance(stmt.value, ast.Constant):
                            down_rev = stmt.value.value
                        elif isinstance(stmt.value, ast.Name) and stmt.value.id == "None":
                            down_rev = None
            if rev is not None:
                revisions[rev] = down_rev

        self.assertIn("0030_purchases_and_entitlement_grants", revisions)
        self.assertEqual(
            revisions["0030_purchases_and_entitlement_grants"],
            "0029_rebase_wi_traffic_downlink",
        )

        # Ensure single root (down_revision is None)
        roots = [rev for rev, down in revisions.items() if down is None]
        self.assertEqual(len(roots), 1, f"Expected 1 root migration, got {roots}")

        # Ensure single head (not referenced as down_revision by any other)
        all_down = set(revisions.values())
        heads = [rev for rev in revisions if rev not in all_down]
        self.assertEqual(len(heads), 1, f"Expected 1 migration head, got {heads}")
        self.assertEqual(heads[0], "0030_purchases_and_entitlement_grants")
