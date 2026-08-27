from bot.constants import NOTIFICATION_INTERVAL
from services.workers.notifications import MAX_RETRY_COUNT, _get_backoff_delay


def test_notification_backoff_preserves_exponential_schedule() -> None:
    expected = [
        NOTIFICATION_INTERVAL,
        NOTIFICATION_INTERVAL * 2,
        NOTIFICATION_INTERVAL * 4,
        NOTIFICATION_INTERVAL * 8,
        NOTIFICATION_INTERVAL * 16,
    ]

    assert [_get_backoff_delay(i) for i in range(MAX_RETRY_COUNT + 1)] == expected


def test_notification_backoff_caps_at_max_retry_count() -> None:
    assert _get_backoff_delay(MAX_RETRY_COUNT + 10) == NOTIFICATION_INTERVAL * (2**MAX_RETRY_COUNT)
