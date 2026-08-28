import datetime
import unittest

from bot import texts
from bot.formatters import format_days_left


class FormattersRegressionTests(unittest.TestCase):
    def test_format_days_left_preserves_permanent_subscription_semantics(self) -> None:
        permanent = datetime.datetime(2100, 1, 1, tzinfo=datetime.timezone.utc)

        self.assertEqual(format_days_left(permanent), texts.TIME_FOREVER)

    def test_format_days_left_keeps_regular_future_subscriptions_finite(self) -> None:
        future = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=30)

        rendered = format_days_left(future)

        self.assertNotEqual(rendered, texts.TIME_FOREVER)
        self.assertIn("30" if "30" in rendered else "29", rendered)
