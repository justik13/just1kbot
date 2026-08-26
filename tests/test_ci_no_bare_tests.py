import ast
import os
import unittest


class TestNoBareTestsGuard(unittest.TestCase):
    def test_no_bare_test_functions_in_test_directory(self):
        """
        Scans all python files in tests/ directory and asserts that no function
        starting with 'test_' is defined at the module level (which means it's a bare
        function not inside a class, rendering it invisible to unittest discover).
        """
        tests_dir = os.path.dirname(__file__)
        bare_tests = []
        for root, dirs, files in os.walk(tests_dir):
            for file in files:
                if file.startswith("test_") and file.endswith(".py"):
                    file_path = os.path.join(root, file)
                    with open(file_path, "r", encoding="utf-8") as f:
                        tree = ast.parse(f.read(), filename=file)
                        for node in tree.body:
                            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                                bare_tests.append(f"{file}:{node.name}")
        
        self.assertEqual(
            len(bare_tests), 0,
            f"Found bare test functions that will be ignored by unittest: {bare_tests}"
        )
