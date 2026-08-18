import unittest

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import CheckConstraint, UniqueConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from database.models import (
    API_OPERATION_STATUSES,
    API_OPERATION_TYPES,
    APIOperation,
)
from utils.encryption import EncryptedString


class APIOperationSchemaTests(unittest.TestCase):
    table = APIOperation.__table__

    def test_columns(self):
        self.assertEqual(
            set(self.table.columns.keys()),
            {
                "id",
                "operation_type",
                "status",
                "idempotency_key",
                "server_id",
                "profile_id",
                "server_name_snapshot",
                "api_url_snapshot",
                "api_key_snapshot",
                "peer_id",
                "client_name",
                "payload",
                "attempts",
                "max_attempts",
                "next_attempt_at",
                "locked_at",
                "locked_by",
                "last_error_code",
                "last_error",
                "created_at",
                "updated_at",
                "completed_at",
            },
        )

    def test_nullability(self):
        required = {
            "id",
            "operation_type",
            "status",
            "idempotency_key",
            "payload",
            "attempts",
            "max_attempts",
            "next_attempt_at",
            "created_at",
            "updated_at",
        }
        nullable = set(self.table.columns.keys()) - required
        self.assertTrue(all(not self.table.c[name].nullable for name in required))
        self.assertTrue(all(self.table.c[name].nullable for name in nullable))

    def _check_constraint(self, name):
        return next(
            constraint
            for constraint in self.table.constraints
            if isinstance(constraint, CheckConstraint) and constraint.name == name
        )

    def test_operation_type_constraint(self):
        sql = str(self._check_constraint("ck_api_operations_operation_type").sqltext)
        self.assertEqual(
            {value for value in API_OPERATION_TYPES if f"'{value}'" in sql},
            set(API_OPERATION_TYPES),
        )

    def test_status_constraint(self):
        sql = str(self._check_constraint("ck_api_operations_status").sqltext)
        self.assertEqual(
            {value for value in API_OPERATION_STATUSES if f"'{value}'" in sql},
            set(API_OPERATION_STATUSES),
        )

    def test_retry_constraints(self):
        constraints = {
            constraint.name: str(constraint.sqltext)
            for constraint in self.table.constraints
            if isinstance(constraint, CheckConstraint)
        }
        self.assertEqual(
            constraints["ck_api_operations_attempts_nonnegative"], "attempts >= 0"
        )
        self.assertEqual(
            constraints["ck_api_operations_max_attempts_positive"], "max_attempts > 0"
        )

    def test_idempotency_unique_constraint(self):
        constraint = next(
            constraint
            for constraint in self.table.constraints
            if isinstance(constraint, UniqueConstraint)
            and constraint.name == "uq_api_operations_idempotency_key"
        )
        self.assertEqual(list(constraint.columns.keys()), ["idempotency_key"])

    def test_foreign_keys_use_set_null(self):
        foreign_keys = {
            column.name: next(iter(column.foreign_keys))
            for column in (self.table.c.server_id, self.table.c.profile_id)
        }
        self.assertEqual(foreign_keys["server_id"].target_fullname, "servers.id")
        self.assertEqual(foreign_keys["profile_id"].target_fullname, "vpn_profiles.id")
        self.assertTrue(
            all(
                foreign_key.ondelete == "SET NULL"
                for foreign_key in foreign_keys.values()
            )
        )

    def test_api_key_snapshot_is_encrypted(self):
        encrypted_type = self.table.c.api_key_snapshot.type
        self.assertIsInstance(encrypted_type, EncryptedString)
        self.assertTrue(encrypted_type.critical)

    def test_payload_uses_jsonb_and_callable_default(self):
        ddl = str(CreateTable(self.table).compile(dialect=postgresql.dialect()))
        self.assertIn("payload JSONB", ddl)
        default = self.table.c.payload.default
        self.assertIsNotNone(default)
        self.assertTrue(default.is_callable)
        first = default.arg(None)
        second = default.arg(None)
        self.assertEqual(first, {})
        self.assertEqual(second, {})
        self.assertIsNot(first, second)

    def test_indexes(self):
        indexes = {index.name: index for index in self.table.indexes}
        expected_columns = {
            "ix_api_operations_claim": ["status", "next_attempt_at", "created_at"],
            "ix_api_operations_processing_lock": ["locked_at"],
            "ix_api_operations_server_id": ["server_id"],
            "ix_api_operations_profile_id": ["profile_id"],
        }
        self.assertEqual(set(indexes), set(expected_columns))
        for name, columns in expected_columns.items():
            self.assertEqual(list(indexes[name].columns.keys()), columns)
        self.assertEqual(
            str(
                indexes["ix_api_operations_claim"]
                .dialect_options["postgresql"]["where"]
            ),
            "status IN ('pending', 'retry')",
        )
        self.assertEqual(
            str(
                indexes["ix_api_operations_processing_lock"]
                .dialect_options["postgresql"]["where"]
            ),
            "status = 'processing'",
        )

    def test_alembic_graph_has_one_head(self):
        scripts = ScriptDirectory.from_config(Config("alembic.ini"))
        self.assertEqual(len(scripts.get_heads()), 1)
        self.assertEqual(scripts.get_heads(), ["0007_webhook_retention"])
        self.assertEqual(scripts.get_bases(), ["0001_clean_baseline"])




if __name__ == "__main__":
    unittest.main()
