import unittest

from bot.middlewares.action_lock import _validate_callback_params


class CallbackValidationTests(unittest.TestCase):
    def test_empty_callback_rejected(self):
        self.assertFalse(_validate_callback_params(""))
        self.assertFalse(_validate_callback_params(None))

    def test_normal_callback_remains_valid(self):
        # Non-prefixed callbacks pass through without numeric check.
        # Note: SQL/command injection via callback_data is not possible — SQLAlchemy
        # uses parameterized queries, so patterns like SELECT/OR etc. are harmless.
        self.assertTrue(_validate_callback_params("select_server:123"))
        self.assertTrue(_validate_callback_params("add_device"))
        self.assertTrue(_validate_callback_params("balance_create:42"))

    def test_numeric_prefix_with_valid_id_accepted(self):
        self.assertTrue(_validate_callback_params("device_id=123"))
        self.assertTrue(_validate_callback_params("devices/456"))
        self.assertTrue(_validate_callback_params("server:7"))
        self.assertTrue(_validate_callback_params("tariff:99"))
        self.assertTrue(_validate_callback_params("user:1001"))

    def test_numeric_prefix_with_non_numeric_id_rejected(self):
        # Non-numeric values after known ID prefixes are rejected as structurally invalid.
        self.assertFalse(_validate_callback_params("devices/abc"))
        self.assertFalse(_validate_callback_params("server:not-a-number"))
        self.assertFalse(_validate_callback_params("tariff:xyz"))
        self.assertFalse(_validate_callback_params("user:bad"))

    def test_add_device_is_locked_action(self):
        from bot.middlewares.action_lock import _is_locked_action
        self.assertTrue(_is_locked_action("add_device"))


if __name__ == "__main__":
    unittest.main()
