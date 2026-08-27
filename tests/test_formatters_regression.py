import datetime

from bot import texts
from bot.formatters import format_days_left


def test_format_days_left_preserves_permanent_subscription_semantics() -> None:
    permanent = datetime.datetime(2100, 1, 1, tzinfo=datetime.timezone.utc)

    assert format_days_left(permanent) == texts.TIME_FOREVER


def test_format_days_left_keeps_regular_future_subscriptions_finite() -> None:
    future = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=30)

    rendered = format_days_left(future)

    assert rendered != texts.TIME_FOREVER
    assert "30" in rendered or "29" in rendered
