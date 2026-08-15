"""Update entitlement_entries check constraints for referral bonuses.

Revision ID: 0004_referral_entitlements
Revises: 0003_add_server_health_fields
Create Date: 2026-08-15 08:00:00.000000
"""

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0004_referral_entitlements"
down_revision: str = "0003_add_server_health_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop old baseline constraints (created via raw SQL in 0001, so use raw ALTER TABLE)
    op.execute(
        "ALTER TABLE public.entitlement_entries "
        "DROP CONSTRAINT IF EXISTS ck_entitlement_entries_shape"
    )
    op.execute(
        "ALTER TABLE public.entitlement_entries "
        "DROP CONSTRAINT IF EXISTS ck_entitlement_entries_type"
    )

    # Add expanded constraints that allow referral bonus entry types
    op.execute(
        "ALTER TABLE public.entitlement_entries "
        "ADD CONSTRAINT ck_entitlement_entries_type "
        "CHECK (entry_type IN ("
        "  'account_purchase_grant', 'referral_user_bonus', "
        "  'referral_referrer_bonus', 'referral_reversal', "
        "  'manual_grant', 'tariff_change'"
        "))"
    )
    op.execute(
        "ALTER TABLE public.entitlement_entries "
        "ADD CONSTRAINT ck_entitlement_entries_shape "
        "CHECK ("
        "  (entry_type IN ('account_purchase_grant', 'referral_user_bonus', 'referral_referrer_bonus', 'manual_grant')"
        "   AND days_delta > 0 AND reversed_entry_id IS NULL"
        "   AND (hours_delta IS NULL OR hours_delta = days_delta * 24))"
        "  OR "
        "  (entry_type = 'tariff_change' AND source_type = 'quote'"
        "   AND days_delta = 0 AND hours_delta > 0 AND reversed_entry_id IS NULL)"
        "  OR "
        "  (entry_type = 'referral_reversal' AND days_delta < 0"
        "   AND reversed_entry_id IS NOT NULL"
        "   AND (hours_delta IS NULL OR hours_delta = days_delta * 24))"
        ")"
    )


def downgrade() -> None:
    # Drop the expanded constraints
    op.execute(
        "ALTER TABLE public.entitlement_entries "
        "DROP CONSTRAINT IF EXISTS ck_entitlement_entries_shape"
    )
    op.execute(
        "ALTER TABLE public.entitlement_entries "
        "DROP CONSTRAINT IF EXISTS ck_entitlement_entries_type"
    )

    # Restore baseline constraints with NOT VALID to avoid scanning existing referral rows
    op.execute(
        "ALTER TABLE public.entitlement_entries "
        "ADD CONSTRAINT ck_entitlement_entries_type "
        "CHECK (entry_type IN ('account_purchase_grant', 'manual_grant', 'tariff_change')) NOT VALID"
    )
    op.execute(
        "ALTER TABLE public.entitlement_entries "
        "ADD CONSTRAINT ck_entitlement_entries_shape "
        "CHECK ("
        "  (entry_type IN ('account_purchase_grant', 'manual_grant')"
        "   AND days_delta > 0 AND reversed_entry_id IS NULL"
        "   AND (hours_delta IS NULL OR hours_delta = days_delta * 24))"
        "  OR "
        "  (entry_type = 'tariff_change' AND source_type = 'quote'"
        "   AND days_delta = 0 AND hours_delta > 0 AND reversed_entry_id IS NULL)"
        ") NOT VALID"
    )
