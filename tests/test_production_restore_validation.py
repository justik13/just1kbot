import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "scripts" / "lib" / "production_restore_runtime.sh"
OVERRIDES = ROOT / "scripts" / "lib" / "production_restore_crash.sh"


class ProductionRestoreValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime = RUNTIME.read_text(encoding="utf-8")
        cls.overrides = OVERRIDES.read_text(encoding="utf-8")

    def test_bounded_staging_validator_overrides_base_implementation(self):
        self.assertIn("restore_staging_database()", self.overrides)
        self.assertIn("to_regclass('public.alembic_version')", self.overrides)
        self.assertIn("SELECT 1 FROM \"{table}\" LIMIT 1", self.overrides)
        self.assertIn("timeout=35", self.overrides)
        self.assertNotIn("SELECT count(*) FROM \"{table}\"", self.overrides)

    def test_override_is_loaded_after_runtime(self):
        engine = (ROOT / "scripts" / "ops" / "production_restore.sh").read_text(
            encoding="utf-8"
        )
        self.assertLess(
            engine.index("production_restore_runtime.sh"),
            engine.index("production_restore_crash.sh"),
        )


if __name__ == "__main__":
    unittest.main()
