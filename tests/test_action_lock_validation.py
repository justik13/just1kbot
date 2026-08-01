import unittest

from bot.middlewares.action_lock import _validate_callback_params


class CallbackValidationTests(unittest.TestCase):
    def test_sql_word_boundaries_reject_injection_fragments(self):
        for callback_data in (
            "device:1 OR 1=1",
            "device:1 AND 1=1",
            "device:1 UNION SELECT",
            "device:1 SELECT value",
        ):
            with self.subTest(callback_data=callback_data):
                self.assertFalse(_validate_callback_params(callback_data))

    def test_normal_callback_remains_valid(self):
        self.assertTrue(_validate_callback_params("select_server:123"))


if __name__ == "__main__":
    unittest.main()
