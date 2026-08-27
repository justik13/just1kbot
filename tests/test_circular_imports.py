import subprocess
import sys
import unittest


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
