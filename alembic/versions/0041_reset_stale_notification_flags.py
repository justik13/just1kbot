"""Reset stale notification flags and unblock falsely flagged users.

Revision ID: 0041_reset_stale_notification_flags
Revises: 0040_vless_traffic_accounting
Create Date: 2026-10-27 12:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0041_reset_stale_notification_flags"
down_revision: str | None = "0040_vless_traffic_accounting"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Reset stuck is_bot_blocked flags for all existing non-deleted, non-banned users
    # so they are returned to worker delivery pools immediately.
    op.execute(
        """
        UPDATE users
        SET is_bot_blocked = false
        WHERE is_deleted = false AND is_banned = false AND is_bot_blocked = true
        """
    )

    # 2. Reset notification debt flags for Standard subscriptions (AWG / VLESS)
    # Tier A: Subscriptions with > 3 days remaining -> reset all notification thresholds
    op.execute(
        """
        UPDATE users
        SET notified_3d = false,
            notified_1d = false,
            notified_2h = false,
            notified_expired = false,
            notified_grace_12h = false,
            notification_retry_count = 0,
            last_notification_attempt = null
        WHERE is_deleted = false
          AND subscription_end IS NOT NULL
          AND subscription_end > now() + interval '3 days'
        """
    )

    # Tier B: Subscriptions with 1 to 3 days remaining -> skip stale 3d, arm 1d and 2h
    op.execute(
        """
        UPDATE users
        SET notified_3d = true,
            notified_1d = false,
            notified_2h = false,
            notified_expired = false,
            notified_grace_12h = false,
            notification_retry_count = 0,
            last_notification_attempt = null
        WHERE is_deleted = false
          AND subscription_end IS NOT NULL
          AND subscription_end <= now() + interval '3 days'
          AND subscription_end > now() + interval '1 day'
        """
    )

    # Tier C: Subscriptions with <= 1 day remaining -> skip stale 3d and 1d, arm 2h
    op.execute(
        """
        UPDATE users
        SET notified_3d = true,
            notified_1d = true,
            notified_2h = false,
            notified_expired = false,
            notified_grace_12h = false,
            notification_retry_count = 0,
            last_notification_attempt = null
        WHERE is_deleted = false
          AND subscription_end IS NOT NULL
          AND subscription_end <= now() + interval '1 day'
          AND subscription_end > now()
        """
    )

    # 3. Reset notification flags for active White Internet subscriptions
    # Tier A: > 3 days remaining
    op.execute(
        """
        UPDATE white_internet_subscriptions
        SET notified_3d = false,
            notified_1d = false,
            notified_2h = false,
            notified_expired = false
        WHERE status = 'ACTIVE'
          AND expires_at IS NOT NULL
          AND expires_at > now() + interval '3 days'
        """
    )

    # Tier B: 1 to 3 days remaining
    op.execute(
        """
        UPDATE white_internet_subscriptions
        SET notified_3d = true,
            notified_1d = false,
            notified_2h = false,
            notified_expired = false
        WHERE status = 'ACTIVE'
          AND expires_at IS NOT NULL
          AND expires_at <= now() + interval '3 days'
          AND expires_at > now() + interval '1 day'
        """
    )

    # Tier C: <= 1 day remaining
    op.execute(
        """
        UPDATE white_internet_subscriptions
        SET notified_3d = true,
            notified_1d = true,
            notified_2h = false,
            notified_expired = false
        WHERE status = 'ACTIVE'
          AND expires_at IS NOT NULL
          AND expires_at <= now() + interval '1 day'
          AND expires_at > now()
        """
    )

    # 4. Normalize legacy device limits: any active Standard subscription must have at least
    # 2 device slots according to the Base tier SSOT (config/tariffs.py).
    op.execute(
        """
        UPDATE users
        SET device_limit = 2
        WHERE is_deleted = false
          AND subscription_end IS NOT NULL
          AND subscription_end > now()
          AND device_limit < 2
        """
    )


def downgrade() -> None:
    # Data migration: values were updated based on current timestamps; no rollback needed.
    pass
