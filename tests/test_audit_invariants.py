"""Unit and behavioral tests for scripts/audit_invariants.py."""

from __future__ import annotations

import os
import subprocess
import sys
import unittest

from scripts.audit_invariants import _PROJECT_ROOT, _inspect_alembic_head, assert_inv_15_alembic_single_head


class AuditInvariantsTests(unittest.IsolatedAsyncioTestCase):
    """Verify invariants script behavior and standalone execution."""

    def test_standalone_execution_without_pythonpath_does_not_fail_on_imports(self):
        """scripts/audit_invariants.py can be imported directly with python without PYTHONPATH set."""
        clean_env = os.environ.copy()
        clean_env.pop("PYTHONPATH", None)

        proc = subprocess.run(
            [
                sys.executable,
                "-c",
                "import runpy; runpy.run_path('scripts/audit_invariants.py', run_name='__not_main__')",
            ],
            cwd=str(_PROJECT_ROOT),
            capture_output=True,
            text=True,
            env=clean_env,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, f"Import failed: {proc.stderr}")
        self.assertNotIn("ModuleNotFoundError", proc.stderr)
        self.assertNotIn("No module named 'config'", proc.stderr)

    def test_alembic_head_inspection_finds_single_head(self):
        """_inspect_alembic_head finds exactly one migration head."""
        heads = _inspect_alembic_head()
        self.assertEqual(len(heads), 1)

    async def test_inv_15_alembic_single_head_passes(self):
        """Invariant 15 passes when alembic graph has single head."""
        res = await assert_inv_15_alembic_single_head()
        self.assertTrue(res.passed)
        self.assertEqual(res.number, 15)
        self.assertIn("Single head verified", res.details)


if __name__ == "__main__":
    unittest.main()
