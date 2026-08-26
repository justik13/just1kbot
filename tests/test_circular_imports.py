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
            "import services.user_cache; import bot.middlewares.user_context; import services.ban_service",
            "import database.models; import config.constants; import database.repositories.servers_repo",
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

        # Subscriptions & Operations
        self.assertEqual(bot.constants.PERMANENT_SUBSCRIPTION_DAYS, config.constants.PERMANENT_SUBSCRIPTION_DAYS)
        self.assertEqual(bot.constants.PERMANENT_END_DATE, config.constants.PERMANENT_END_DATE)
        self.assertEqual(bot.constants.GRACE_PERIOD_HOURS, config.constants.GRACE_PERIOD_HOURS)
        self.assertEqual(bot.constants.DEVICE_DAILY_LIMIT, config.constants.DEVICE_DAILY_LIMIT)
        self.assertEqual(bot.constants.STALE_PAYMENT_THRESHOLD, config.constants.STALE_PAYMENT_THRESHOLD)
        self.assertEqual(bot.constants.WORKER_ERROR_SLEEP_INTERVAL, config.constants.WORKER_ERROR_SLEEP_INTERVAL)
        self.assertEqual(bot.constants.NOTIFICATION_INTERVAL, config.constants.NOTIFICATION_INTERVAL)
        self.assertEqual(bot.constants.TRAFFIC_SYNC_INTERVAL, config.constants.TRAFFIC_SYNC_INTERVAL)
        self.assertEqual(bot.constants.API_CONCURRENCY_LIMIT, config.constants.API_CONCURRENCY_LIMIT)
        self.assertEqual(bot.constants.API_RETRY_COUNT, config.constants.API_RETRY_COUNT)
        self.assertEqual(bot.constants.API_TIMEOUT, config.constants.API_TIMEOUT)
        self.assertEqual(bot.constants.TELEGRAM_MESSAGE_LIMIT, config.constants.TELEGRAM_MESSAGE_LIMIT)
        self.assertEqual(bot.constants.HUB_CACHE_MAX_SIZE, config.constants.HUB_CACHE_MAX_SIZE)
        self.assertEqual(bot.constants.HUB_CACHE_TTL, config.constants.HUB_CACHE_TTL)
        self.assertEqual(bot.constants.USER_CONTEXT_CACHE_MAX_SIZE, config.constants.USER_CONTEXT_CACHE_MAX_SIZE)
        self.assertEqual(bot.constants.USER_CONTEXT_CACHE_TTL, config.constants.USER_CONTEXT_CACHE_TTL)

        # ServerHealthState
        self.assertEqual(bot.constants.ServerHealthState.ONLINE, "ONLINE")
        self.assertEqual(bot.constants.ServerHealthState.AUTO_DISABLED, "AUTO_DISABLED")

    def test_downward_dependency_ast_scan(self):
        """Verify statically via AST that lower architectural layers do not import forbidden upward modules."""
        project_root = Path(__file__).resolve().parent.parent

        # Tier 1: config and database must NEVER import ANY bot modules
        tier_1_dirs = ["config", "database"]
        tier_1_violations = []
        for dir_name in tier_1_dirs:
            target_dir = project_root / dir_name
            if not target_dir.exists():
                continue
            for py_file in target_dir.rglob("*.py"):
                try:
                    with open(py_file, "r", encoding="utf-8") as f:
                        tree = ast.parse(f.read(), filename=str(py_file))
                except Exception as exc:
                    self.fail(f"Failed to parse {py_file}: {exc}")

                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            if alias.name == "bot" or alias.name.startswith("bot."):
                                tier_1_violations.append((str(py_file.relative_to(project_root)), node.lineno, alias.name))
                    elif isinstance(node, ast.ImportFrom):
                        module_name = node.module or ""
                        if module_name == "bot" or module_name.startswith("bot."):
                            tier_1_violations.append((str(py_file.relative_to(project_root)), node.lineno, module_name))

        self.assertEqual(
            tier_1_violations,
            [],
            f"Found Tier 1 (config/database) upward imports to bot.*:\n{tier_1_violations}",
        )

        # Tier 2: Core services and utils must NEVER import UI state, filters, handlers, or middlewares
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

                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            for forbidden in tier_2_forbidden:
                                if alias.name == forbidden or alias.name.startswith(forbidden + "."):
                                    tier_2_violations.append((str(py_file.relative_to(project_root)), node.lineno, alias.name))
                    elif isinstance(node, ast.ImportFrom):
                        module_name = node.module or ""
                        for forbidden in tier_2_forbidden:
                            if module_name == forbidden or module_name.startswith(forbidden + "."):
                                tier_2_violations.append((str(py_file.relative_to(project_root)), node.lineno, module_name))

        self.assertEqual(
            tier_2_violations,
            [],
            f"Found Tier 2 (services/utils) upward imports violating clean layer decoupling:\n{tier_2_violations}",
        )

