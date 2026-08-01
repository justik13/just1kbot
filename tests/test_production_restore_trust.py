import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
OVERRIDES = ROOT / "scripts" / "lib" / "production_restore_crash.sh"


class ProductionRestoreTrustBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = OVERRIDES.read_text(encoding="utf-8")

    def test_root_executed_paths_require_root_ownership_and_safe_modes(self):
        for marker in (
            "validate_root_owned_regular_file",
            "validate_root_owned_directory",
            "is not root-owned",
            "writable by group/other",
            'validate_root_owned_regular_file "$ENV_FILE"',
            'validate_root_owned_regular_file "$VERIFY_BACKUP"',
            'validate_root_owned_regular_file "$HEALTHCHECK_COMMAND"',
            'validate_root_owned_regular_file "$VENV_DIR/bin/alembic"',
            'validate_root_owned_regular_file "$PROJECT_DIR/alembic.ini"',
        ):
            self.assertIn(marker, self.text)

    def test_alembic_must_remain_executable(self):
        self.assertIn('[[ -x "$VENV_DIR/bin/alembic" ]]', self.text)


if __name__ == "__main__":
    unittest.main()
