import ast
import unittest
from pathlib import Path

class LegacyDeletionSafetyTests(unittest.TestCase):
    def test_legacy_worker_has_no_amnezia_write(self):
        path = Path(__file__).parents[1] / "services/workers/cleanup.py"
        tree = ast.parse(path.read_text())
        forbidden = {"create_user", "create_user_result", "update_client", "update_client_result", "delete_user", "delete_user_result"}
        calls = {n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
        self.assertFalse(calls & forbidden)

    def test_legacy_worker_quarantines(self):
        source = (Path(__file__).parents[1] / "services/workers/cleanup.py").read_text()
        self.assertIn("legacy worker disabled", source)
        self.assertIn("row.attempts = -1", source)
