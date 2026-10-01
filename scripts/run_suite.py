"""Test suite runner with module exclusion support and empty-suite safeguards."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import unittest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def build_suite(
    start_dir: str = "tests",
    pattern: str = "test_*.py",
    exclude_modules: set[str] | None = None,
) -> unittest.TestSuite:
    """Discover and return a filtered unittest.TestSuite."""
    loader = unittest.TestLoader()
    suite = loader.discover(start_dir, pattern=pattern)
    if not exclude_modules:
        return suite

    def _is_excluded(mod_name: str) -> bool:
        return any(
            mod_name == ex or mod_name.endswith(f".{ex}")
            for ex in exclude_modules
        )

    def _filter(node: unittest.TestSuite | unittest.TestCase) -> unittest.TestSuite | unittest.TestCase | None:
        if isinstance(node, unittest.TestSuite):
            filtered_children = []
            for child in node:
                res = _filter(child)
                if res is not None:
                    filtered_children.append(res)
            return unittest.TestSuite(filtered_children)
        else:
            mod_name = getattr(node, "__module__", "")
            if _is_excluded(mod_name):
                return None
            return node

    result = _filter(suite)
    return result if isinstance(result, unittest.TestSuite) else unittest.TestSuite()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run unittest discovery suite with module exclusion support"
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="Module name to exclude from discovery (can be passed multiple times)",
    )
    parser.add_argument(
        "--start-dir",
        default="tests",
        help="Directory to start test discovery (default: tests)",
    )
    parser.add_argument(
        "--pattern",
        default="test_*.py",
        help="File pattern to match test files (default: test_*.py)",
    )
    parser.add_argument(
        "--min-tests",
        type=int,
        default=1,
        help="Minimum number of tests required to run (default: 1)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose test output",
    )
    args = parser.parse_args(argv)

    suite = build_suite(
        start_dir=args.start_dir,
        pattern=args.pattern,
        exclude_modules=set(args.exclude),
    )

    test_count = suite.countTestCases()
    if test_count < args.min_tests:
        sys.stderr.write(
            f"Error: Discovered {test_count} tests, which is less than min-tests threshold ({args.min_tests}). "
            "Aborting to prevent false-green CI.\n"
        )
        return 1

    runner = unittest.TextTestRunner(verbosity=2 if args.verbose else 1)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
