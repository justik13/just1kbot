import ast
import io
import logging
import unittest
from pathlib import Path

from utils.logging_security import (
    SensitiveDataFilter,
    install_sensitive_data_filter,
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

    def test_authorization_headers_are_fully_redacted(self):
        cases = (
            (
                "Authorization: Basic dXNlcjpwYXNzd29yZA==",
                "dXNlcjpwYXNzd29yZA==",
            ),
            (
                "Proxy-Authorization: Basic PROXY_BASIC_CANARY_Zm9vOmJhcg==",
                "PROXY_BASIC_CANARY_Zm9vOmJhcg==",
            ),
            (
                "Authorization: Digest username=admin, response=DIGEST_CANARY_741",
                "DIGEST_CANARY_741",
            ),
            (
                "Authorization: ApiKey CUSTOM_AUTH_CANARY_852",
                "CUSTOM_AUTH_CANARY_852",
            ),
        )
        for header, secret in cases:
            with self.subTest(header=header.split(":", 1)[0], secret=secret):
                output = self._capture(lambda logger, value=header: logger.info(value))
                self.assertNotIn(secret, output)

    def test_header_dictionary_repr_is_fully_redacted(self):
        basic = "DICT_BASIC_CANARY_dXNlcjpwYXNz=="
        digest = "DICT_DIGEST_RESPONSE_CANARY_963"
        headers = {
            "Authorization": f"Basic {basic}",
            "Proxy-Authorization": f"Digest username=admin, response={digest}",
        }
        output = self._capture(lambda logger: logger.warning("headers=%r", headers))
        self.assertNotIn(basic, output)
        self.assertNotIn(digest, output)

    def test_token_only_url_userinfo_is_removed(self):
        token = "TOKEN_ONLY_USERINFO_CANARY_174"
        output = self._capture(
            lambda logger: logger.error(
                "upstream=https://%s@example.com/private host=example.com",
                token,
            )
        )
        self.assertNotIn(token, output)
        self.assertIn("example.com/private", output)
        self.assertIn("host=example.com", output)

    def test_cookie_headers_are_fully_redacted_without_losing_safe_context(self):
        cookie_one = "COOKIE_ONE_CANARY_285"
        cookie_two = "COOKIE_TWO_CANARY_396"
        set_cookie = "SET_COOKIE_CANARY_407"
        output = self._capture(
            lambda logger: logger.info(
                "Cookie: session=%s; csrftoken=%s\n"
                "status=401 payment_id=pay-cookie-safe\n"
                "Set-Cookie: session=%s; HttpOnly; Path=/",
                cookie_one,
                cookie_two,
                set_cookie,
            )
        )
        self.assertNotIn(cookie_one, output)
        self.assertNotIn(cookie_two, output)
        self.assertNotIn(set_cookie, output)
        self.assertIn("status=401", output)
        self.assertIn("payment_id=pay-cookie-safe", output)

    def test_raw_headers_redact_values_named_like_safe_log_fields(self):
        cases = (
            (
                "Cookie: session=SAFE_FIRST_CANARY_101; "
                "status=COOKIE_STATUS_CANARY_202; "
                "csrftoken=COOKIE_SECOND_CANARY_303",
                (
                    "SAFE_FIRST_CANARY_101",
                    "COOKIE_STATUS_CANARY_202",
                    "COOKIE_SECOND_CANARY_303",
                ),
            ),
            (
                "Cookie: host=COOKIE_HOST_CANARY_404; "
                "request_id=COOKIE_REQUEST_CANARY_505",
                ("COOKIE_HOST_CANARY_404", "COOKIE_REQUEST_CANARY_505"),
            ),
            (
                "Set-Cookie: status=SET_COOKIE_STATUS_CANARY_606; "
                "HttpOnly; Path=/",
                ("SET_COOKIE_STATUS_CANARY_606",),
            ),
            (
                "Authorization: Digest username=admin, "
                "host=AUTH_HOST_CANARY_707, response=AUTH_RESPONSE_CANARY_808",
                ("AUTH_HOST_CANARY_707", "AUTH_RESPONSE_CANARY_808"),
            ),
            (
                "Proxy-Authorization: Custom "
                "request_id=PROXY_REQUEST_CANARY_909 "
                "token=PROXY_TOKEN_CANARY_010",
                ("PROXY_REQUEST_CANARY_909", "PROXY_TOKEN_CANARY_010"),
            ),
        )
        for header, secrets in cases:
            with self.subTest(header=header.split(":", 1)[0]):
                output = self._capture(
                    lambda logger, value=header: logger.warning(value)
                )
                for secret in secrets:
                    self.assertNotIn(secret, output)

    def test_raw_header_name_collisions_are_redacted_in_traceback(self):
        cookie_secrets = (
            "TRACE_COOKIE_SESSION_CANARY_121",
            "TRACE_COOKIE_STATUS_CANARY_232",
            "TRACE_COOKIE_SECOND_CANARY_343",
        )
        auth_secrets = (
            "TRACE_AUTH_HOST_CANARY_454",
            "TRACE_AUTH_RESPONSE_CANARY_565",
        )
        message = (
            f"Cookie: session={cookie_secrets[0]}; "
            f"status={cookie_secrets[1]}; csrftoken={cookie_secrets[2]}\n"
            "safe_context_on_own_line=true\n"
            "Authorization: Digest username=admin, "
            f"host={auth_secrets[0]}, response={auth_secrets[1]}"
        )

        def log_failure(logger):
            try:
                raise RuntimeError(message)
            except RuntimeError:
                logger.exception(
                    "request_id=req-collision-safe "
                    "payment_id=pay-collision-safe "
                    "operation_id=op-collision-safe"
                )

        output = self._capture(log_failure)
        for secret in (*cookie_secrets, *auth_secrets):
            self.assertNotIn(secret, output)
        self.assertIn("RuntimeError", output)
        self.assertIn("Traceback (most recent call last)", output)
        self.assertIn("request_id=req-collision-safe", output)
        self.assertIn("payment_id=pay-collision-safe", output)
        self.assertIn("operation_id=op-collision-safe", output)

    def test_secret_query_parameters_are_redacted(self):
        parameters = (
            ("token", "QUERY_TOKEN_CANARY_518"),
            ("access_token", "QUERY_ACCESS_CANARY_629"),
            ("api_key", "QUERY_API_KEY_CANARY_730"),
            ("password", "QUERY_PASSWORD_CANARY_841"),
            ("secret", "QUERY_SECRET_CANARY_952"),
        )
        url = "https://api.example/path?" + "&".join(
            f"{name}={secret}" for name, secret in parameters
        )
        output = self._capture(
            lambda logger: logger.info(
                "request_id=req-query-safe url=%s status=200", url
            )
        )
        for _, secret in parameters:
            self.assertNotIn(secret, output)
        self.assertIn("api.example/path", output)
        self.assertIn("request_id=req-query-safe", output)
        self.assertIn("status=200", output)

    def test_urlsafe_fernet_key_is_redacted_but_safe_ids_remain(self):
        key = "AAA-_AABAgMEBQYHCAkKCwwNDg8QERITFBUWFxgZGhs="
        git_sha = "0123456789abcdef0123456789abcdef01234567"
        output = self._capture(
            lambda logger: logger.info(
                "key=%s request_id=req-fernet-safe operation_id=op-fernet-safe "
                "commit=%s server_id=77",
                key,
                git_sha,
            )
        )
        self.assertNotIn(key, output)
        self.assertIn("request_id=req-fernet-safe", output)
        self.assertIn("operation_id=op-fernet-safe", output)
        self.assertIn(git_sha, output)
        self.assertIn("server_id=77", output)

    def test_headers_urls_queries_and_fernet_key_are_redacted_in_traceback(self):
        secrets = (
            "TRACE_BASIC_CANARY_dXNlcjpwYXNz==",
            "TRACE_COOKIE_ONE_CANARY_163",
            "TRACE_COOKIE_TWO_CANARY_274",
            "TRACE_QUERY_CANARY_385",
            "TRACE_TOKEN_USERINFO_CANARY_496",
            "AAA-_AABAgMEBQYHCAkKCwwNDg8QERITFBUWFxgZGhs=",
        )
        message = (
            f"Authorization: Basic {secrets[0]}\n"
            f"Cookie: session={secrets[1]}; csrftoken={secrets[2]}\n"
            f"url=https://{secrets[4]}@api.trace/path?access_token={secrets[3]}\n"
            f"DB_ENCRYPTION_KEY={secrets[5]}"
        )

        def log_failure(logger):
            try:
                raise RuntimeError(message)
            except RuntimeError:
                logger.exception(
                    "request_id=req-trace-safe payment_id=pay-trace-safe "
                    "operation_id=op-trace-safe profile_id=profile-trace-safe "
                    "server_id=88 host=api.trace"
                )

        output = self._capture(log_failure)
        for secret in secrets:
            self.assertNotIn(secret, output)
        for safe in (
            "RuntimeError",
            "req-trace-safe",
            "pay-trace-safe",
            "op-trace-safe",
            "profile-trace-safe",
            "server_id=88",
            "host=api.trace",
        ):
            self.assertIn(safe, output)

    def test_installed_filter_protects_propagated_child_logs(self):
        root_logger = logging.getLogger("tests.production_topology")
        child_logger = logging.getLogger("tests.production_topology.child")
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        old_handlers = root_logger.handlers[:]
        old_filters = root_logger.filters[:]
        old_level = root_logger.level
        old_propagate = root_logger.propagate
        old_child_handlers = child_logger.handlers[:]
        old_child_level = child_logger.level
        old_child_propagate = child_logger.propagate
        try:
            root_logger.handlers = [handler]
            root_logger.filters = []
            root_logger.setLevel(logging.INFO)
            root_logger.propagate = False
            child_logger.handlers = []
            child_logger.setLevel(logging.INFO)
            child_logger.propagate = True
            install_sensitive_data_filter(root_logger)

            secret = "TOPOLOGY_BASIC_CANARY_dXNlcjpwYXNz=="
            child_logger.warning(
                "status=403 request_id=req-topology-safe Authorization: Basic %s",
                secret,
            )
            handler.flush()
            output = stream.getvalue()
            self.assertNotIn(secret, output)
            self.assertIn("status=403", output)
            self.assertIn("request_id=req-topology-safe", output)
        finally:
            root_logger.handlers = old_handlers
            root_logger.filters = old_filters
            root_logger.setLevel(old_level)
            root_logger.propagate = old_propagate
            child_logger.handlers = old_child_handlers
            child_logger.setLevel(old_child_level)
            child_logger.propagate = old_child_propagate

    def test_white_internet_and_vpn_uris_are_redacted(self):
        cases = (
            "incy://add/https://cdn.example.com/sub/TOKEN_CANARY_123",
            "incy://crypt1/SECRET_PAYLOAD_CANARY_456",
            "clash://install-config?url=https://example.com/canary",
            "sing-box://import-remote?url=https://example.com/canary",
            "shadowrocket://add/sub?url=https://example.com/canary",
            "vless://user@host:443?encryption=none",
        )
        for uri in cases:
            with self.subTest(uri=uri):
                output = self._capture(lambda logger, val=uri: logger.info("vpn=%s", val))
                self.assertNotIn("TOKEN_CANARY_123", output)
                self.assertNotIn("SECRET_PAYLOAD_CANARY_456", output)
                self.assertIn("[VPN_URI_REDACTED]", output)

    def test_age_and_yookassa_keys_are_redacted(self):
        age_key = "AGE-SECRET-KEY-1" + "A" * 58
        yoo_live = "live_" + "B" * 35
        yoo_test = "test_" + "C" * 35
        output = self._capture(
            lambda logger: logger.warning(
                "age=%s yoo_live=%s yoo_test=%s req=req-safe-123",
                age_key,
                yoo_live,
                yoo_test,
            )
        )
        self.assertNotIn(age_key, output)
        self.assertNotIn(yoo_live, output)
        self.assertNotIn(yoo_test, output)
        self.assertIn("[AGE_KEY_REDACTED]", output)
        self.assertIn("[YOOKASSA_KEY_REDACTED]", output)
        self.assertIn("req=req-safe-123", output)

    def test_settings_repr_masks_database_and_redis_passwords(self):
        from config.settings import Settings
        settings = Settings(
            BOT_TOKEN="123456:FAKE_TOKEN",
            ADMIN_IDS=[123456789],
            SUPPORT_USERNAME="fake_support",
            DATABASE_URL="postgresql+asyncpg://super_user:SUPER_SECRET_DB_PASS@127.0.0.1:5432/db",
            DB_ENCRYPTION_KEY="MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
            REDIS_URL="redis://:SUPER_SECRET_REDIS_PASS@127.0.0.1:6379/0",
            REDIS_PASSWORD="SUPER_SECRET_REDIS_PASS",
            YOOKASSA_SHOP_ID="123456",
            YOOKASSA_SECRET_KEY="test_secret_key_long_enough_val",
            YOOKASSA_RETURN_URL="https://t.me/{bot_username}",
            YOOKASSA_WEBHOOK_PORT=8080,
            DOMAIN="vpn.example.test",
            SSL_EMAIL="admin@realdomain.test",
        )
        rendered = repr(settings)
        self.assertNotIn("SUPER_SECRET_DB_PASS", rendered)
        self.assertNotIn("SUPER_SECRET_REDIS_PASS", rendered)
        self.assertNotIn("DATABASE_URL", rendered)
        self.assertNotIn("REDIS_URL", rendered)
        self.assertNotIn("REDIS_PASSWORD", rendered)

    def test_camel_and_kebab_case_secret_keys_are_redacted(self):
        cases = (
            ("secretKey: SECRET_VAL_1", "SECRET_VAL_1"),
            ("secret_key: SECRET_VAL_2", "SECRET_VAL_2"),
            ("privateKey: PRIV_VAL_1", "PRIV_VAL_1"),
            ("private_key: PRIV_VAL_2", "PRIV_VAL_2"),
            ("clientSecret: CLIENT_SEC_1", "CLIENT_SEC_1"),
            ("client_secret: CLIENT_SEC_2", "CLIENT_SEC_2"),
            ("redisPassword: REDIS_PASS_1", "REDIS_PASS_1"),
            ("redis_password: REDIS_PASS_2", "REDIS_PASS_2"),
            ("dbPassword: DB_PASS_1", "DB_PASS_1"),
            ("db_password: DB_PASS_2", "DB_PASS_2"),
            ("presharedKey: PSK_VAL_1", "PSK_VAL_1"),
            ("preshared_key: PSK_VAL_2", "PSK_VAL_2"),
            ("xApiKey: X_API_KEY_1", "X_API_KEY_1"),
            ("x-api-key: X_API_KEY_2", "X_API_KEY_2"),
            ("x_api_key: X_API_KEY_3", "X_API_KEY_3"),
            ("databaseUrl: DB_URL_1", "DB_URL_1"),
            ("database_url: DB_URL_2", "DB_URL_2"),
        )
        for text, secret in cases:
            with self.subTest(text=text):
                output = self._capture(lambda logger, val=text: logger.info(val))
                self.assertNotIn(secret, output)

    def test_post_quantum_age_secret_keys_are_redacted(self):
        pq_age_key = "AGE-SECRET-KEY-PQ-1" + "Z" * 65
        classical_age_key = "AGE-SECRET-KEY-1" + "X" * 58
        output = self._capture(
            lambda logger: logger.warning(
                "classical=%s pq=%s status=ok",
                classical_age_key,
                pq_age_key,
            )
        )
        self.assertNotIn(pq_age_key, output)
        self.assertNotIn(classical_age_key, output)
        self.assertIn("status=ok", output)

    def test_extra_attributes_are_sanitized_by_filter(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)

        class CustomFormatter(logging.Formatter):
            def format(self, record):
                return (
                    f"{record.getMessage()} extra_token={getattr(record, 'extra_token', None)} "
                    f"payload={getattr(record, 'payload', None)}"
                )

        handler.setFormatter(CustomFormatter())
        handler.addFilter(SensitiveDataFilter())
        logger = logging.getLogger(f"tests.extra.{id(stream)}")
        logger.handlers = [handler]
        logger.propagate = False
        logger.setLevel(logging.INFO)

        raw_token = "123456:TELEGRAM_EXTRA_CANARY_TOKEN_abcdefghijklmnopqrstuvwxyz"
        raw_key = "AGE-SECRET-KEY-PQ-1" + "W" * 60

        logger.info(
            "event=test",
            extra={
                "extra_token": raw_token,
                "payload": {"apiKey": raw_key, "safe_id": 42},
            },
        )
        output = stream.getvalue()
        self.assertNotIn(raw_token, output)
        self.assertNotIn(raw_key, output)
        self.assertIn("safe_id", output)
        self.assertIn("42", output)

    def test_xray_node_client_error_response_is_sanitized_and_truncated(self):
        from utils.logging_security import sanitize_short
        long_secret_text = "Error detail: " + "AGE-SECRET-KEY-1" + "K" * 58 + " " + "x" * 300
        result = f"HTTP 500: {sanitize_short(long_secret_text, limit=200)}"
        self.assertNotIn("AGE-SECRET-KEY-1", result)
        self.assertTrue(len(result) <= 250)
        self.assertTrue(result.endswith("..."))

    def test_quoted_secrets_with_special_characters_and_spaces_are_redacted(self):
        cases = (
            ('password="secret;123"', "secret;123"),
            ("token='my token'", "my token"),
            ('password="complex ; & $ pass"', "complex ; & $ pass"),
            ("client_secret='secret;with;semicolons'", "secret;with;semicolons"),
            ('api_key="key with spaces and = ; signs"', "key with spaces and = ; signs"),
            ('{"password": "secret;123", "user": "safe_user"}', "secret;123"),
            ("DATABASE_URL='postgresql://user:p;ass@host:5432/db'", "p;ass"),
            ('password="escaped\\"quote;and;semi"', 'escaped\\"quote;and;semi'),
            ("passwd='single\\'escaped;semi'", "single\\'escaped;semi"),
            ('secret="value:with:colons,and,commas;and;semi"', "value:with:colons,and,commas;and;semi"),
            ('access_token="tok_12345;special#@!"', "tok_12345;special#@!"),
            ('{"token": "secret token with spaces"}', "secret token with spaces"),
            ('{ "password" : "secret;123" }', "secret;123"),
            ('token=abc_123-xyz', "abc_123-xyz"),
            ('secret=""', None),
            ("secret=''", None),
        )
        for text, secret in cases:
            with self.subTest(text=text):
                output = self._capture(lambda logger, val=text: logger.info("data: %s", val))
                if secret:
                    self.assertNotIn(secret, output)
                self.assertIn("[REDACTED]", output)

        # Verify safe context is preserved around quoted secrets
        output = self._capture(
            lambda logger: logger.info('host=auth.local password="secret;123" user=alice status=ok')
        )
        self.assertNotIn("secret;123", output)
        self.assertIn("host=auth.local", output)
        self.assertIn("user=alice", output)
        self.assertIn("status=ok", output)

        # Verify multiple quoted secrets on one line
        output_multi = self._capture(
            lambda logger: logger.info('data: password="foo;bar" token=\'tok en;123\'')
        )
        self.assertNotIn("foo;bar", output_multi)
        self.assertNotIn("tok en;123", output_multi)
        self.assertEqual(output_multi.count("[REDACTED]"), 2)

    def test_key_overmatching_is_prevented_by_boundary(self):
        # Keys like 'token' or 'secret' should not match inside longer words
        output = self._capture(
            lambda logger: logger.info("safe_mytoken=allowed_value item_not_secret=visible")
        )
        self.assertIn("allowed_value", output)
        self.assertIn("visible", output)
        self.assertNotIn("[REDACTED]", output)


class NoDirectSettingsSecretLoggingTests(unittest.TestCase):
    """Prevent Settings secret values from being passed to logging calls."""

    FORBIDDEN_FIELDS = {
        "BOT_TOKEN",
        "DATABASE_URL",
        "REDIS_URL",
        "REDIS_PASSWORD",
        "DB_ENCRYPTION_KEY",
        "DB_ENCRYPTION_KEYS",
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
        for package in ("bot", "database", "integrations", "services", "utils"):
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
