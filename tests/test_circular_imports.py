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

    @staticmethod
    def _find_illegal_bot_imports_for_services(tree: ast.AST) -> list[tuple[int, str]]:
        """Flag bot.* imports that are not the data-only facades bot.texts/bot.constants.

        Pure services may depend on the canonical text/constants catalogues (pure data,
        no aiogram, no upward imports) but must never touch presentation/behavioural
        layers (bot.handlers, bot.middlewares, bot.states, bot.main, bot.keyboards).
        """
        allowed_modules = {"bot.texts", "bot.constants"}
        violations: list[tuple[int, str]] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "bot":
                        violations.append((node.lineno, alias.name))
                    elif alias.name.startswith("bot.") and alias.name.rsplit(".", 1)[0] not in allowed_modules:
                        violations.append((node.lineno, alias.name))
            elif isinstance(node, ast.ImportFrom):
                module_name = node.module or ""
                if module_name == "bot":
                    for alias in node.names:
                        if alias.name not in {"texts", "constants"}:
                            violations.append((node.lineno, f"from bot import {alias.name}"))
                elif module_name.startswith("bot.") and module_name.rsplit(".", 1)[0] not in allowed_modules:
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
                    if arg_val == "bot" or (
                        arg_val.startswith("bot.")
                        and arg_val.rsplit(".", 1)[0] not in allowed_modules
                    ):
                        violations.append((node.lineno, arg_val))
        return violations

    def test_downward_dependency_ast_scan(self):
        """Verify statically via AST that lower architectural layers obey strict boundary rules.

        Architecture Invariants:
        1. Pure core layers: config/, database/, integrations/, utils/ must NEVER import ANY
           module from the bot layer (bot, bot.texts, bot.keyboards, bot.handlers, etc.).
        2. Pure services (services/*.py) may import ONLY the data-only facades bot.texts and
           bot.constants (no aiogram, no upward imports); bot.handlers/bot.middlewares/
           bot.states/bot.main/bot.keyboards remain forbidden.
        3. Delivery adapter workers: services/workers/* may import only canonical presentation
           adapters (bot.texts.* and bot.keyboards.*), but must NEVER import bot.handlers,
           bot.middlewares, or bot.states.
        """
        project_root = Path(__file__).resolve().parent.parent
        violations = []

        # 1. Pure core modules (MUST NEVER import bot.*)
        pure_bot_free_dirs = ["config", "database", "integrations", "utils"]
        for dir_name in pure_bot_free_dirs:
            target_dir = project_root / dir_name
            if not target_dir.exists():
                continue
            for py_file in target_dir.rglob("*.py"):
                with open(py_file, "r", encoding="utf-8") as f:
                    tree = ast.parse(f.read(), filename=str(py_file))
                file_violations = self._find_ast_import_violations(tree, ("bot",))
                for lineno, name in file_violations:
                    violations.append((str(py_file.relative_to(project_root)), lineno, name))

        # 2. Pure services in services/*.py (excluding workers subdirectory)
        #    May import only the data-only facades bot.texts / bot.constants.
        services_dir = project_root / "services"
        if services_dir.exists():
            for py_file in services_dir.glob("*.py"):
                with open(py_file, "r", encoding="utf-8") as f:
                    tree = ast.parse(f.read(), filename=str(py_file))
                file_violations = self._find_illegal_bot_imports_for_services(tree)
                for lineno, name in file_violations:
                    violations.append((str(py_file.relative_to(project_root)), lineno, name))

        # 2. Worker layer (services/workers/*): may ONLY import bot.texts and bot.keyboards
        workers_dir = services_dir / "workers"
        if workers_dir.exists():
            forbidden_for_workers = ("bot.handlers", "bot.middlewares", "bot.states", "bot.main")
            for py_file in workers_dir.rglob("*.py"):
                with open(py_file, "r", encoding="utf-8") as f:
                    tree = ast.parse(f.read(), filename=str(py_file))
                file_violations = self._find_ast_import_violations(tree, forbidden_for_workers)
                for lineno, name in file_violations:
                    violations.append((str(py_file.relative_to(project_root)), lineno, name))

        self.assertEqual(
            violations,
            [],
            f"Strict Architectural Firewall Violation: Found illegal upward imports:\n{violations}",
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
