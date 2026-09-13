"""Backfill active subscriptions into entitlement entries with exact hours and honest legacy provenance.

Revision ID: 0027_backfill_entitlements
Revises: 0026_wi_trial_semantics
Create Date: 2026-09-12 21:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0027_backfill_entitlements"
down_revision: str | None = "0026_wi_trial_semantics"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Update check constraint to strictly preserve days_delta * 24 invariant while permitting sub-day (days_delta = 0, hours_delta > 0)
    op.execute("ALTER TABLE entitlement_entries DROP CONSTRAINT IF EXISTS ck_entitlement_entries_shape")
    op.execute(
        """
        ALTER TABLE entitlement_entries ADD CONSTRAINT ck_entitlement_entries_shape CHECK (
          (entry_type IN ('account_purchase_grant', 'referral_user_bonus', 'referral_referrer_bonus', 'manual_grant')
           AND reversed_entry_id IS NULL
           AND (
             (days_delta = 0 AND hours_delta > 0)
             OR
             (days_delta > 0 AND (hours_delta IS NULL OR hours_delta = days_delta * 24))
           ))
          OR 
          (entry_type = 'tariff_change' AND source_type = 'quote'
           AND days_delta = 0 AND hours_delta > 0 AND reversed_entry_id IS NULL)
          OR 
          (entry_type = 'referral_reversal' AND days_delta < 0
           AND reversed_entry_id IS NOT NULL
           AND (hours_delta IS NULL OR hours_delta = days_delta * 24))
        )
        """
    )

    # 2. Backfill active users who have NO existing entitlement entries.
    # We do NOT invent false payment links from wallet top-ups; legacy active subscriptions
    # are recorded honestly as admin/legacy manual grants so balance projection is tracked and non-destructive.
    op.execute(
        """
        WITH missing_users AS (
            SELECT 
                u.id AS user_id,
                u.device_limit,
                u.current_tariff_id,
                u.subscription_end,
                GREATEST(1, CEIL(EXTRACT(EPOCH FROM (u.subscription_end - NOW())) / 3600)::int) AS exact_hours
            FROM users u
            WHERE u.subscription_end > NOW()
              AND u.is_deleted = false
              AND NOT EXISTS (
                  SELECT 1 FROM entitlement_entries e
                  WHERE e.beneficiary_user_id = u.id
              )
        )
        INSERT INTO entitlement_entries (
            beneficiary_user_id,
            source_type,
            source_id,
            entry_type,
            days_delta,
            hours_delta,
            device_limit_snapshot,
            tariff_id_snapshot,
            metadata,
            created_at
        )
        SELECT 
            mu.user_id,
            'admin',
            'legacy_0027_grant_' || mu.user_id,
            'manual_grant',
            CASE WHEN mu.exact_hours % 24 = 0 THEN mu.exact_hours / 24 ELSE 0 END,
            mu.exact_hours,
            COALESCE(mu.device_limit, 1),
            mu.current_tariff_id,
            jsonb_build_object('reason', 'legacy_active_subscription_backfill'),
            mu.subscription_end - (mu.exact_hours * INTERVAL '1 hour')
        FROM missing_users mu
        ON CONFLICT (beneficiary_user_id, source_type, source_id, entry_type) DO NOTHING
        """
    )


def downgrade() -> None:
    # 1. Clean up only the records created by this migration
    op.execute(
        """
        DELETE FROM entitlement_entries
        WHERE source_type = 'admin' AND source_id LIKE 'legacy_0027_grant_%'
        """
    )
    # 2. Restore strict pre-0027 constraint
    op.execute("ALTER TABLE entitlement_entries DROP CONSTRAINT IF EXISTS ck_entitlement_entries_shape")
    op.execute(
        """
        ALTER TABLE entitlement_entries ADD CONSTRAINT ck_entitlement_entries_shape CHECK (
          (entry_type IN ('account_purchase_grant', 'referral_user_bonus', 'referral_referrer_bonus', 'manual_grant')
           AND days_delta > 0 AND reversed_entry_id IS NULL
           AND (hours_delta IS NULL OR hours_delta = days_delta * 24))
          OR 
          (entry_type = 'tariff_change' AND source_type = 'quote'
           AND days_delta = 0 AND hours_delta > 0 AND reversed_entry_id IS NULL)
          OR 
          (entry_type = 'referral_reversal' AND days_delta < 0
           AND reversed_entry_id IS NOT NULL
           AND (hours_delta IS NULL OR hours_delta = days_delta * 24))
        )
        """
    )
