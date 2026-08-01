import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
ENGINE = ROOT / "scripts" / "ops" / "production_restore.sh"
INPUT_GUARD = ROOT / "scripts" / "lib" / "production_restore_input.sh"


class ProductionRestoreInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = ENGINE.read_text(encoding="utf-8")
        cls.guard = INPUT_GUARD.read_text(encoding="utf-8")

    def test_input_guard_is_loaded_after_base_actions(self):
        self.assertTrue(INPUT_GUARD.is_file())
        self.assertLess(
            self.engine.index("production_restore_actions.sh"),
            self.engine.index("production_restore_input.sh"),
        )
        self.assertLess(
            self.engine.index("production_restore_input.sh"),
            self.engine.index("production_restore_crash.sh"),
        )

    def test_artifact_sidecar_and_identity_are_root_owned_and_private(self):
        for marker in (
            "validate_restore_input_file",
            "is not root-owned",
            "permissions are too broad",
            'validate_restore_input_file "$ARTIFACT" \'backup artifact\' 600',
            'validate_restore_input_file "$ARTIFACT.sha256"',
            'validate_restore_input_file "$AGE_IDENTITY_FILE"',
            "base_extract_and_verify_backup",
        ):
            self.assertIn(marker, self.guard)


if __name__ == "__main__":
    unittest.main()
