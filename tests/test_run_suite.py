"""Tests for scripts/run_suite.py."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from scripts.run_suite import build_suite, main


class TestRunSuite(unittest.TestCase):
    def test_build_suite_without_exclusion_finds_tests(self):
        suite = build_suite(start_dir="tests", pattern="test_user_text_audit.py")
        self.assertGreater(suite.countTestCases(), 0)

    def test_build_suite_with_exclusion_filters_out_module(self):
        suite_all = build_suite(start_dir="tests", pattern="test_user_text_audit.py")
        count_all = suite_all.countTestCases()
        self.assertGreater(count_all, 0)

        suite_excluded = build_suite(
            start_dir="tests",
            pattern="test_user_text_audit.py",
            exclude_modules={"test_user_text_audit"},
        )
        self.assertEqual(suite_excluded.countTestCases(), 0)

    def test_main_cli_returns_zero_on_success(self):
        test_args = [
            "run_suite.py",
            "--start-dir",
            "tests",
            "--pattern",
            "test_user_text_audit.py",
        ]
        with patch("sys.argv", test_args):
            exit_code = main()
        self.assertEqual(exit_code, 0)

    def test_main_cli_returns_one_on_failure(self):
        with patch("scripts.run_suite.build_suite"), \
             patch("unittest.TextTestRunner.run") as mock_run:
            mock_res = MagicMock()
            mock_res.wasSuccessful.return_value = False
            mock_run.return_value = mock_res
            test_args = ["run_suite.py", "--pattern", "dummy.py"]
            with patch("sys.argv", test_args):
                exit_code = main()
            self.assertEqual(exit_code, 1)


if __name__ == "__main__":
    unittest.main()
