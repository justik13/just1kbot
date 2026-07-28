import time
import unittest
from datetime import datetime, timedelta, timezone

from bot.handlers.webhook import (
    WEBHOOK_MAX_FUTURE_SKEW_SECONDS,
    _is_recent_timestamp,
)


class WebhookTimestampTests(unittest.TestCase):
    def test_accepts_recent_unix_timestamp(self):
        self.assertTrue(_is_recent_timestamp(str(time.time() - 10)))

    def test_rejects_expired_timestamp(self):
        self.assertFalse(_is_recent_timestamp(str(time.time() - 301)))

    def test_rejects_timestamp_too_far_in_future(self):
        future = time.time() + WEBHOOK_MAX_FUTURE_SKEW_SECONDS + 10
        self.assertFalse(_is_recent_timestamp(str(future)))

    def test_rejects_future_iso_timestamp(self):
        future = datetime.now(timezone.utc) + timedelta(minutes=5)
        self.assertFalse(_is_recent_timestamp(future.isoformat()))

    def test_rejects_invalid_timestamp(self):
        self.assertFalse(_is_recent_timestamp("not-a-timestamp"))


if __name__ == "__main__":
    unittest.main()
