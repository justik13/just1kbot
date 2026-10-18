"""Unit tests for admin operation idempotency repository."""

import hashlib
import unittest

from database.repositories.idempotency_repo import make_admin_op_key


class IdempotencyRepoTests(unittest.TestCase):
    """Test suite for idempotency key generator."""

    def test_make_admin_op_key_format_and_determinism(self):
        action = "balance_adjust"
        admin_id = 123456
        target_id = 987654
        chat_id = 123456
        message_id = 42
        value = 100

        key1 = make_admin_op_key(action, admin_id, target_id, chat_id, message_id, value)
        key2 = make_admin_op_key(action, admin_id, target_id, chat_id, message_id, value)

        self.assertEqual(key1, key2)
        self.assertEqual(len(key1), 64)

        expected_raw = f"v1|{action}|{admin_id}|{target_id}|{chat_id}|{message_id}|{value}"
        expected_hash = hashlib.sha256(expected_raw.encode("utf-8")).hexdigest()
        self.assertEqual(key1, expected_hash)

    def test_make_admin_op_key_uniqueness_across_parameters(self):
        base = ("balance_adjust", 123, 456, 123, 10, "100")
        base_key = make_admin_op_key(*base)

        # Different value
        diff_val_key = make_admin_op_key("balance_adjust", 123, 456, 123, 10, "300")
        self.assertNotEqual(base_key, diff_val_key)

        # Different message_id
        diff_msg_key = make_admin_op_key("balance_adjust", 123, 456, 123, 11, "100")
        self.assertNotEqual(base_key, diff_msg_key)

        # Different admin_id
        diff_admin_key = make_admin_op_key("balance_adjust", 124, 456, 123, 10, "100")
        self.assertNotEqual(base_key, diff_admin_key)

        # Different chat_id
        diff_chat_key = make_admin_op_key("balance_adjust", 123, 456, 124, 10, "100")
        self.assertNotEqual(base_key, diff_chat_key)

        # Different target_id
        diff_target_key = make_admin_op_key("balance_adjust", 123, 457, 123, 10, "100")
        self.assertNotEqual(base_key, diff_target_key)
