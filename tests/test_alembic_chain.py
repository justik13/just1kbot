import os
import re
import unittest
from alembic.config import Config
from alembic.script import ScriptDirectory


class AlembicChainTests(unittest.TestCase):
    def test_alembic_chain_has_single_head_and_valid_revisions(self):
        config = Config("alembic.ini")
        script = ScriptDirectory.from_config(config)
        heads = script.get_heads()
        self.assertEqual(len(heads), 1, f"Expected 1 alembic head, got {heads}")
        self.assertEqual(heads[0], "0030_simple_billing")

        # Check all revisions have identifier length <= 32 chars
        versions_dir = "alembic/versions"
        for fname in os.listdir(versions_dir):
            if not fname.endswith(".py"):
                continue
            path = os.path.join(versions_dir, fname)
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            match = re.search(r'revision:\s*str\s*=\s*["\']([^"\']+)["\']', content)
            if match:
                rev_id = match.group(1)
                self.assertLessEqual(
                    len(rev_id),
                    32,
                    f"Alembic revision '{rev_id}' in {fname} exceeds 32 chars (len={len(rev_id)})",
                )
