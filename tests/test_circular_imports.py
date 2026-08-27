import ast
import subprocess
import sys
import unittest
from pathlib import Path


class CircularImportRegressionTests(unittest.TestCase):
    """Verify key application entrypoints, constants, and modules import cleanly in isolated Python runtimes."""

    def _assert_isolated_code(self, python_code: str) -> None:
        cmd = [sys.executable, "-c", python_code]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        self.assertEqual(
            proc.returncode,
            0,
            f"Failed to execute isolated code:\nCODE:\n{python_code}\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}",
        )

    def test_config_constants_import(self):
        self._assert_isolated_code("import config.constants")

    def test_services_user_cache_import(self):
        self._assert_isolated_code("import services.user_cache")

    def test_bot_main_import(self):
        self._assert_isolated_code("import bot.main")

    def test_bot_webhook_handler_import(self):
        self._assert_isolated_code("import bot.handlers.webhook")

    def test_utils_http_rate_limiter_import(self):
        self._assert_isolated_code("import utils.http_rate_limiter")

    def test_utils_vpn_helpers_import(self):
        self._assert_isolated_code("import utils.vpn_helpers")

    def test_integrations_package_import(self):
        self._assert_isolated_code("import integrations")

    def test_amnezia_bridge_web_routes_import(self):
        self._assert_isolated_code("import integrations.amnezia_bridge.web_routes")

    def test_services_amnezia_bridge_constants_import(self):
        self._assert_isolated_code("import services.amnezia_bridge_constants")

    def test_database_repositories_clean_imports(self):
        self._assert_isolated_code("import database.repositories.users_repo; import database.repositories.servers_repo")

    def test_services_clean_imports(self):
        self._assert_isolated_code("import services.subscription; import services.device_service; import services.ban_service")

    def test_order_dependent_import_permutations(self):
        permutations = [
            "import integrations; import utils.vpn_helpers; import bot.main",
            "import utils.vpn_helpers; import integrations; import bot.main",
            "import bot.handlers.webhook; import utils.http_rate_limiter; import integrations",
            "import services.amnezia_bridge_constants; import utils.http_rate_limiter; import bot.main",
            "import integrations.amnezia_bridge.constants; import bot.constants; import utils.vpn_helpers",
            "import config.constants; import utils.http_rate_limiter; import integrations.amnezia_bridge.constants",
            "import integrations.amnezia_bridge.web_routes; import bot.constants; import utils.http_rate_limiter",
        ]
        for code in permutations:
            with self.subTest(code=code):
                self._assert_isolated_code(code)

    def test_architectural_single_source_of_truth(self):
        import bot.constants
        import config.constants
        import integrations.amnezia_bridge.constants
        import services.amnezia_bridge_constants
        import utils.http_rate_limiter
        import utils.vpn_helpers

        # Protocol
        self.assertEqual(config.constants.AMNEZIA_PROTOCOL, "amneziawg2")
        self.assertEqual(bot.constants.AMNEZIA_PROTOCOL, config.constants.AMNEZIA_PROTOCOL)
        self.assertEqual(utils.vpn_helpers.AMNEZIA_PROTOCOL, config.constants.AMNEZIA_PROTOCOL)

        # Config Size limit
        self.assertEqual(config.constants.MAX_RAW_CONFIG_BYTES, 65536)
        self.assertEqual(bot.constants.MAX_RAW_CONFIG_BYTES, config.constants.MAX_RAW_CONFIG_BYTES)
        self.assertEqual(integrations.amnezia_bridge.constants.MAX_RAW_CONFIG_BYTES, config.constants.MAX_RAW_CONFIG_BYTES)
        self.assertEqual(services.amnezia_bridge_constants.MAX_RAW_CONFIG_BYTES, config.constants.MAX_RAW_CONFIG_BYTES)
        self.assertEqual(utils.vpn_helpers.MAX_RAW_CONFIG_BYTES, config.constants.MAX_RAW_CONFIG_BYTES)

        # Rate limiter defaults
        self.assertEqual(config.constants.RATE_LIMIT_REQUESTS_PER_MINUTE, 30.0)
        self.assertEqual(config.constants.RATE_LIMIT_BURST, 10)
        self.assertEqual(integrations.amnezia_bridge.constants.RATE_LIMIT_REQUESTS_PER_MINUTE, 30.0)
        self.assertEqual(integrations.amnezia_bridge.constants.RATE_LIMIT_BURST, 10)
        self.assertEqual(services.amnezia_bridge_constants.RATE_LIMIT_REQUESTS_PER_MINUTE, 30.0)
        self.assertEqual(services.amnezia_bridge_constants.RATE_LIMIT_BURST, 10)
        self.assertEqual(utils.http_rate_limiter.amnezia_bridge_rate_limiter.burst, 10.0)
        self.assertEqual(utils.http_rate_limiter.amnezia_bridge_rate_limiter.rate, 0.5)  # 30.0 / 60.0

    @staticmethod
    def _find_ast_import_violations(tree: ast.AST, forbidden_prefixes: tuple[str, ...]) -> list[tuple[int, str]]:
        violations = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    for forbidden in forbidden_prefixes:
                        if alias.name == forbidden or alias.name.startswith(forbidden + "."):
                            violations.append((node.lineno, alias.name))
            elif isinstance(node, ast.ImportFrom):
                module_name = node.module or ""
                for forbidden in forbidden_prefixes:
                    if module_name == forbidden or module_name.startswith(forbidden + "."):
                        violations.append((node.lineno, module_name))
            elif isinstance(node, ast.Call):
                if (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr == "import_module"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                ):
                    arg_val = node.args[0].value
                    for forbidden in forbidden_prefixes:
                        if arg_val == forbidden or arg_val.startswith(forbidden + "."):
                            violations.append((node.lineno, arg_val))
        return violations

    def test_downward_dependency_ast_scan(self):
        """Verify statically via AST that lower architectural layers do not import forbidden upward modules."""
        project_root = Path(__file__).resolve().parent.parent

        # Tier 1: config, database, and integrations/amnezia_bridge must NEVER import ANY bot modules
        tier_1_dirs = ["config", "database", "integrations/amnezia_bridge"]
        tier_1_violations = []
        for dir_rel in tier_1_dirs:
            target_dir = project_root / dir_rel
            if not target_dir.exists():
                continue
            for py_file in target_dir.rglob("*.py"):
                try:
                    with open(py_file, "r", encoding="utf-8") as f:
                        tree = ast.parse(f.read(), filename=str(py_file))
                except Exception as exc:
                    self.fail(f"Failed to parse {py_file}: {exc}")

                file_violations = self._find_ast_import_violations(tree, ("bot",))
                for lineno, name in file_violations:
                    tier_1_violations.append((str(py_file.relative_to(project_root)), lineno, name))

        self.assertEqual(
            tier_1_violations,
            [],
            f"Found Tier 1 (config/database/bridge) upward imports to bot.*:\n{tier_1_violations}",
        )

        # Tier 2: Core services and utils must NEVER import UI state, filters, handlers, middlewares, or constants
        tier_2_forbidden = (
            "bot.middlewares",
            "bot.states",
            "bot.filters",
            "bot.handlers",
            "bot.constants",
        )
        tier_2_dirs = ["utils", "services"]
        tier_2_violations = []

        for dir_name in tier_2_dirs:
            target_dir = project_root / dir_name
            if not target_dir.exists():
                continue
            for py_file in target_dir.rglob("*.py"):
                try:
                    with open(py_file, "r", encoding="utf-8") as f:
                        tree = ast.parse(f.read(), filename=str(py_file))
                except Exception as exc:
                    self.fail(f"Failed to parse {py_file}: {exc}")

                file_violations = self._find_ast_import_violations(tree, tier_2_forbidden)
                for lineno, name in file_violations:
                    tier_2_violations.append((str(py_file.relative_to(project_root)), lineno, name))

        self.assertEqual(
            tier_2_violations,
            [],
            f"Found Tier 2 (services/utils) upward imports violating clean layer decoupling:\n{tier_2_violations}",
        )

    def test_ast_guard_detects_deliberate_violation(self):
        """Negative self-test proving that the AST scanner detects direct, aliased, and dynamic upward imports."""
        bad_code_samples = [
            ("import bot.constants", ("bot",)),
            ("from bot.middlewares.user_context import invalidate_user_cache", ("bot.middlewares",)),
            ("from bot import texts", ("bot",)),
            ("import bot", ("bot",)),
            ("importlib.import_module('bot.handlers')", ("bot.handlers",)),
        ]
        for snippet, forbidden in bad_code_samples:
            with self.subTest(code=snippet):
                tree = ast.parse(snippet)
                violations = self._find_ast_import_violations(tree, forbidden)
                self.assertGreater(
                    len(violations),
                    0,
                    f"AST guard failed to detect deliberate violation in: {snippet!r}",
                )
