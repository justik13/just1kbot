"""Test text catalog consistency after removing dead constants."""

import unittest
from bot import texts


class TestTextsConsistency(unittest.TestCase):
    def test_new_white_internet_texts_exist(self):
        self.assertTrue(hasattr(texts, "BTN_WL_CONFIRM_RENEW"))
        self.assertTrue(hasattr(texts, "BTN_WL_PAY_BASE"))
        self.assertTrue(hasattr(texts, "BTN_WL_PAY_PACK"))
        self.assertTrue(hasattr(texts, "BTN_WL_TOPUP_SHORTAGE"))
        self.assertTrue(hasattr(texts, "BTN_WL_RETURN_TO_SERVICE"))
        self.assertTrue(hasattr(texts, "BTN_WL_CONVERT_TRIAL"))
        self.assertTrue(hasattr(texts, "BTN_WL_REFRESH_STATUS"))
        self.assertTrue(hasattr(texts, "WL_TRIAL_CANNOT_TOPUP"))
        self.assertTrue(hasattr(texts, "WL_AUTO_PUSH_READY"))
        self.assertTrue(hasattr(texts, "WL_BUY_PREVIEW_TEXT"))
        self.assertTrue(hasattr(texts, "WL_RENEW_PREVIEW_TEXT"))
        self.assertTrue(hasattr(texts, "WL_TOPUP_PREVIEW_TEXT"))
        self.assertTrue(hasattr(texts, "WL_PREVIEW_BALANCE_OK"))
        self.assertTrue(hasattr(texts, "WL_PREVIEW_BALANCE_SHORTAGE"))

    def test_dead_white_internet_texts_removed(self):
        self.assertFalse(hasattr(texts, "WL_TRIAL_FINISHED"))
        self.assertFalse(hasattr(texts, "WL_PAID_FEATURES_DISABLED_ALERT"))


if __name__ == "__main__":
    unittest.main()
