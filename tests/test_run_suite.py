"""Isolated tests for scripts/run_suite.py."""

from __future__ import annotations

import io
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts.run_suite import build_suite, main


class TestRunSuite(unittest.TestCase):
    def setUp(self):
        for mod in ["test_alpha", "test_alphabeta", "test_failing"]:
            sys.modules.pop(mod, None)
        self.test_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.test_dir, ignore_errors=True)
        self.addCleanup(self._cleanup_modules)

        # Create two test modules: test_alpha.py and test_alphabeta.py
        p_alpha = Path(self.test_dir) / "test_alpha.py"
        p_alpha.write_text(
            "import unittest\n"
            "class AlphaTest(unittest.TestCase):\n"
            "    def test_one(self): pass\n"
            "    def test_two(self): pass\n",
            encoding="utf-8",
        )
        p_beta = Path(self.test_dir) / "test_alphabeta.py"
        p_beta.write_text(
            "import unittest\n"
            "class BetaTest(unittest.TestCase):\n"
            "    def test_three(self): pass\n",
            encoding="utf-8",
        )

    def _cleanup_modules(self):
        for mod in ["test_alpha", "test_alphabeta", "test_failing"]:
            sys.modules.pop(mod, None)

    def test_build_suite_without_exclusion_finds_all_tests(self):
        suite = build_suite(start_dir=self.test_dir, pattern="test_*.py")
        self.assertEqual(suite.countTestCases(), 3)

    def test_strict_module_exclusion_does_not_substring_match(self):
        # Exclude 'test_alpha'. It must NOT exclude 'test_alphabeta'.
        suite = build_suite(
            start_dir=self.test_dir,
            pattern="test_*.py",
            exclude_modules={"test_alpha"},
        )
        self.assertEqual(suite.countTestCases(), 1)

    def test_main_cli_fails_if_zero_tests_discovered(self):
        # Exclude both modules -> 0 tests
        stderr_buf = io.StringIO()
        with patch("sys.stderr", stderr_buf):
            exit_code = main([
                "--start-dir", self.test_dir,
                "--exclude", "test_alpha",
                "--exclude", "test_alphabeta",
            ])
        self.assertEqual(exit_code, 1)
        self.assertIn("less than min-tests threshold", stderr_buf.getvalue())

    def test_main_cli_returns_zero_on_success(self):
        exit_code = main([
            "--start-dir", self.test_dir,
            "--exclude", "test_alpha",
            "--min-tests", "1",
        ])
        self.assertEqual(exit_code, 0)

    def test_main_cli_returns_one_on_test_failure(self):
        p_fail = Path(self.test_dir) / "test_failing.py"
        p_fail.write_text(
            "import unittest\n"
            "class FailTest(unittest.TestCase):\n"
            "    def test_fail(self): self.fail('forced failure')\n",
            encoding="utf-8",
        )
        null_buf = io.StringIO()
        with patch("sys.stderr", null_buf):
            exit_code = main([
                "--start-dir", self.test_dir,
                "--pattern", "test_failing.py",
            ])
        self.assertEqual(exit_code, 1)


if __name__ == "__main__":
    unittest.main()
