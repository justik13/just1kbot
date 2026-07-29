import ast
import io
import logging
from pathlib import Path
import unittest

from utils.logging_security import (
    SensitiveDataFilter,
    safe_url_target,
    sanitize_text,
)


class LoggingSecurityTests(unittest.TestCase):
    def _capture(self, callback) -> str:
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        handler.addFilter(SensitiveDataFilter())
        logger = logging.getLogger(f"tests.secret_canary.{id(stream)}")
        logger.handlers = [handler]
        logger.propagate = False
        logger.setLevel(logging.INFO)
        callback(logger)
        handler.flush()
        return stream.getvalue()

    def test_all_required_secret_forms_are_removed_from_formed_log_message(self):
        canaries = {
            "postgres_user": "pg_canary_user",
            "postgres_password": "PG_CANARY_PASSWORD_913",
            "redis_password": "REDIS_CANARY_PASSWORD_624",
            "bearer": "BEARER_CANARY_TOKEN_735",
            "telegram": "123456789:TELEGRAM_CANARY_TOKEN_abcdefghijklmnopqrstuvwxyz",
            "api_key": "X_API_CANARY_KEY_846",
            "vpn": "VPN_CANARY_CONFIG_957",
        }
        postgres = (
            f"postgresql+asyncpg://{canaries['postgres_user']}:"
            f"{canaries['postgres_password']}@db.internal:5432/projectx?ssl=require"
        )
        redis = f"redis://:{canaries['redis_password']}@redis.internal:6379/0"
        vpn_uri = f"vpn://{canaries['vpn']}?name=phone"

        output = self._capture(
            lambda logger: logger.warning(
                "request_id=%s host=%s status=%d payment_id=%s operation_id=%s "
                "profile_id=%s server_id=%d postgres=%s redis=%s "
                "Authorization: Bearer %s BOT_TOKEN=%s headers=%r vpn=%s",
                "req-canary-123",
                "api.safe.internal",
                401,
                "pay-safe-uuid",
                "op-safe-uuid",
                "profile-safe-uuid",
                42,
                postgres,
                redis,
                canaries["bearer"],
                canaries["telegram"],
                {"x-api-key": canaries["api_key"]},
                vpn_uri,
            )
        )

        for secret in canaries.values():
            self.assertNotIn(secret, output)
        for safe_value in (
            "req-canary-123",
            "api.safe.internal",
            "401",
            "pay-safe-uuid",
            "op-safe-uuid",
            "profile-safe-uuid",
            "42",
        ):
            self.assertIn(safe_value, output)

    def test_exception_traceback_is_preserved_but_sanitized(self):
        password = "TRACEBACK_URL_PASSWORD_CANARY_159"
        secret_url = f"postgresql://trace_user:{password}@db.safe:5432/projectx"

        def log_failure(logger):
            try:
                raise RuntimeError(f"database unavailable: {secret_url}")
            except RuntimeError:
                logger.exception(
                    "operation failed operation_id=%s", "op-visible-123"
                )

        output = self._capture(log_failure)
        self.assertNotIn(password, output)
        self.assertNotIn("trace_user", output)
        self.assertIn("RuntimeError", output)
        self.assertIn("Traceback (most recent call last)", output)
        self.assertIn("operation_id=op-visible-123", output)
        self.assertIn("db.safe:5432/projectx", output)

    def test_alert_sanitizer_uses_the_same_rules(self):
        secret = "ALERT_BEARER_CANARY_268"
        text = sanitize_text(f"upstream rejected Authorization: Bearer {secret}")
        self.assertNotIn(secret, text)
        self.assertIn("upstream rejected", text)

    def test_safe_url_target_drops_credentials_path_and_query(self):
        target = safe_url_target(
            "https://api-user:API_PASSWORD_CANARY@"
            "vpn.safe:8443/v1?token=QUERY_CANARY"
        )
        self.assertEqual("vpn.safe:8443", target)


class NoDirectSettingsSecretLoggingTests(unittest.TestCase):
    """Prevent Settings secret values from being passed to logging calls."""

    FORBIDDEN_FIELDS = {
        "BOT_TOKEN",
        "DATABASE_URL",
        "REDIS_URL",
        "DB_ENCRYPTION_KEY",
        "YOOKASSA_SECRET_KEY",
    }
    LOG_METHODS = {
        "debug",
        "info",
        "warning",
        "warn",
        "error",
        "exception",
        "critical",
    }

    def test_secret_settings_attributes_are_not_logging_arguments(self):
        root = Path(__file__).resolve().parents[1]
        violations = []
        for package in ("bot", "database", "services", "utils"):
            for path in (root / package).rglob("*.py"):
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
                    if not (
                        isinstance(call.func, ast.Attribute)
                        and call.func.attr in self.LOG_METHODS
                    ):
                        continue
                    values = [*call.args, *(keyword.value for keyword in call.keywords)]
                    leaked = {
                        node.attr
                        for value in values
                        for node in ast.walk(value)
                        if isinstance(node, ast.Attribute)
                        and node.attr in self.FORBIDDEN_FIELDS
                    }
                    if leaked:
                        violations.append(
                            f"{path.relative_to(root)}:{call.lineno}: {sorted(leaked)}"
                        )
        self.assertEqual([], violations, "Direct secret logging:\n" + "\n".join(violations))


if __name__ == "__main__":
    unittest.main()
