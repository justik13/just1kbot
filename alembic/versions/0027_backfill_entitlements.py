"""Backfill active subscriptions into entitlement entries with exact hours and payment linking.

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
    # 1. Update check constraint to allow exact hours_delta without strict days_delta * 24
    op.execute("ALTER TABLE entitlement_entries DROP CONSTRAINT IF EXISTS ck_entitlement_entries_shape")
    op.execute(
        """
        ALTER TABLE entitlement_entries ADD CONSTRAINT ck_entitlement_entries_shape CHECK (
          (entry_type IN ('account_purchase_grant', 'referral_user_bonus', 'referral_referrer_bonus', 'manual_grant')
           AND days_delta >= 0 AND reversed_entry_id IS NULL
           AND ((hours_delta IS NULL AND days_delta > 0) OR hours_delta > 0))
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

    # 2. Fix existing rows created via previous manual backfill queries (source_id LIKE 'legacy_backfill%')
    op.execute(
        """
        WITH user_target AS (
            SELECT 
                e.id AS entry_id,
                u.id AS user_id,
                u.subscription_end,
                GREATEST(1, FLOOR(EXTRACT(EPOCH FROM (u.subscription_end - NOW())) / 3600)::int) AS exact_hours,
                p.id AS payment_id,
                p.amount AS payment_amount,
                COALESCE(p.paid_at, p.credited_at, p.created_at) AS payment_time
            FROM entitlement_entries e
            JOIN users u ON u.id = e.beneficiary_user_id
            LEFT JOIN LATERAL (
                SELECT p.id, p.amount, p.paid_at, p.credited_at, p.created_at
                FROM payments p
                WHERE p.user_id = u.id AND p.provider_status = 'succeeded'
                ORDER BY COALESCE(p.paid_at, p.credited_at, p.created_at) DESC
                LIMIT 1
            ) p ON true
            WHERE e.source_type = 'admin'
              AND (e.source_id LIKE 'legacy_backfill%' OR e.source_id = 'legacy_backfill')
              AND u.subscription_end > NOW()
        )
        UPDATE entitlement_entries e
        SET 
            hours_delta = ut.exact_hours,
            days_delta = ut.exact_hours / 24,
            created_at = ut.subscription_end - (ut.exact_hours * INTERVAL '1 hour'),
            metadata = CASE 
                WHEN ut.payment_id IS NOT NULL THEN 
                    jsonb_build_object(
                        'reason', 'legacy_payment_backfill',
                        'payment_id', ut.payment_id,
                        'payment_amount', ut.payment_amount,
                        'payment_time', ut.payment_time
                    )
                ELSE 
                    jsonb_build_object('reason', 'legacy_admin_grant_backfill')
            END
        FROM user_target ut
        WHERE e.id = ut.entry_id
        """
    )

    # 3. Backfill active users who have NO existing entitlement entries
    op.execute(
        """
        WITH missing_users AS (
            SELECT 
                u.id AS user_id,
                u.device_limit,
                u.current_tariff_id,
                u.subscription_end,
                GREATEST(1, FLOOR(EXTRACT(EPOCH FROM (u.subscription_end - NOW())) / 3600)::int) AS exact_hours,
                p.id AS payment_id,
                p.amount AS payment_amount,
                COALESCE(p.paid_at, p.credited_at, p.created_at) AS payment_time
            FROM users u
            LEFT JOIN LATERAL (
                SELECT p.id, p.amount, p.paid_at, p.credited_at, p.created_at
                FROM payments p
                WHERE p.user_id = u.id AND p.provider_status = 'succeeded'
                ORDER BY COALESCE(p.paid_at, p.credited_at, p.created_at) DESC
                LIMIT 1
            ) p ON true
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
            CASE WHEN mu.payment_id IS NOT NULL THEN 'payment' ELSE 'admin' END,
            CASE 
                WHEN mu.payment_id IS NOT NULL THEN 'legacy_payment_' || mu.payment_id
                ELSE 'legacy_admin_grant_' || mu.user_id
            END,
            'manual_grant',
            mu.exact_hours / 24,
            mu.exact_hours,
            COALESCE(mu.device_limit, 1),
            mu.current_tariff_id,
            CASE 
                WHEN mu.payment_id IS NOT NULL THEN 
                    jsonb_build_object(
                        'reason', 'legacy_payment_backfill',
                        'payment_id', mu.payment_id,
                        'payment_amount', mu.payment_amount,
                        'payment_time', mu.payment_time
                    )
                ELSE 
                    jsonb_build_object('reason', 'legacy_admin_grant_backfill')
            END,
            mu.subscription_end - (mu.exact_hours * INTERVAL '1 hour')
        FROM missing_users mu
        ON CONFLICT (beneficiary_user_id, source_type, source_id, entry_type) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM entitlement_entries
        WHERE (source_type = 'admin' AND (source_id LIKE 'legacy_admin_grant_%' OR source_id LIKE 'legacy_backfill%' OR source_id = 'legacy_backfill'))
           OR (source_type = 'payment' AND source_id LIKE 'legacy_payment_%')
        """
    )
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
