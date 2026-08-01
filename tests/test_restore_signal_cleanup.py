import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
REHEARSAL = ROOT / "scripts" / "ops" / "restore_rehearsal.sh"


class RestoreSignalCleanupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = REHEARSAL.read_text(encoding="utf-8")

    def test_exit_cleanup_and_signal_exit_codes_are_explicit(self):
        self.assertIn("trap finish EXIT", self.text)
        self.assertIn("trap 'exit 130' INT", self.text)
        self.assertIn("trap 'exit 143' TERM", self.text)
        self.assertLess(self.text.index("trap finish EXIT"), self.text.index("for command in"))


if __name__ == "__main__":
    unittest.main()
