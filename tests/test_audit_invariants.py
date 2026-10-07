"""Unit and behavioral tests for scripts/audit_invariants.py."""

from __future__ import annotations

import os
import subprocess
import sys
import unittest

from unittest.mock import AsyncMock, MagicMock
from sqlalchemy.ext.asyncio import AsyncSession

from scripts.audit_invariants import (
    _PROJECT_ROOT,
    _inspect_alembic_head,
    assert_inv_10_account_balance_non_negativity,
    assert_inv_15_alembic_single_head,
)


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

    async def test_inv_10_account_balance_passes_when_no_violations(self):
        """Invariant 10 passes when query returns no violations."""
        session = AsyncMock(spec=AsyncSession)
        mock_res = MagicMock()
        mock_res.all.return_value = []
        session.execute.return_value = mock_res

        res = await assert_inv_10_account_balance_non_negativity(session)
        self.assertTrue(res.passed)
        self.assertEqual(res.number, 10)

    async def test_inv_10_account_balance_fails_when_violations_exist(self):
        """Invariant 10 fails when query returns violations with unexplained deficit."""
        session = AsyncMock(spec=AsyncSession)
        mock_res = MagicMock()
        mock_res.all.return_value = [(101, -500, 0)]
        session.execute.return_value = mock_res

        res = await assert_inv_10_account_balance_non_negativity(session)
        self.assertFalse(res.passed)
        self.assertEqual(res.number, 10)
        self.assertIn("1 users with unexplained negative balance", res.details)


if __name__ == "__main__":
    unittest.main()
