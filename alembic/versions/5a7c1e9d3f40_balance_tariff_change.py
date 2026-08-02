"""Settle tariff changes from account balance with hour precision.

Revision ID: 5a7c1e9d3f40
Revises: 4f2a9d6c1e30
"""

import sqlalchemy as sa
from alembic import op


revision = "5a7c1e9d3f40"
down_revision = "4f2a9d6c1e30"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "entitlement_entries",
        sa.Column("hours_delta", sa.Integer(), nullable=True),
    )
    op.execute(
        "UPDATE entitlement_entries SET hours_delta = days_delta * 24"
    )
    op.drop_constraint(
        "ck_entitlement_entries_type",
        "entitlement_entries",
        type_="check",
    )
    op.drop_constraint(
        "ck_entitlement_entries_shape",
        "entitlement_entries",
        type_="check",
    )
    op.create_check_constraint(
        "ck_entitlement_entries_type",
        "entitlement_entries",
        "entry_type IN ('payment_grant','account_purchase_grant',"
        "'referral_user_bonus','referral_referrer_bonus','payment_reversal',"
        "'referral_reversal','manual_grant','tariff_change')",
    )
    op.create_check_constraint(
        "ck_entitlement_entries_shape",
        "entitlement_entries",
        "(entry_type IN ('payment_grant','account_purchase_grant',"
        "'referral_user_bonus','referral_referrer_bonus','manual_grant') "
        "AND days_delta > 0 AND reversed_entry_id IS NULL "
        "AND (hours_delta IS NULL OR hours_delta = days_delta * 24)) OR "
        "(entry_type = 'tariff_change' AND source_type = 'quote' "
        "AND days_delta = 0 AND hours_delta > 0 "
        "AND reversed_entry_id IS NULL) OR "
        "(entry_type IN ('payment_reversal','referral_reversal') "
        "AND days_delta < 0 AND reversed_entry_id IS NOT NULL "
        "AND (hours_delta IS NULL OR hours_delta = days_delta * 24))",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_entitlement_entries_shape",
        "entitlement_entries",
        type_="check",
    )
    op.drop_constraint(
        "ck_entitlement_entries_type",
        "entitlement_entries",
        type_="check",
    )
    op.execute("DELETE FROM entitlement_entries WHERE entry_type='tariff_change'")
    op.create_check_constraint(
        "ck_entitlement_entries_type",
        "entitlement_entries",
        "entry_type IN ('payment_grant','account_purchase_grant',"
        "'referral_user_bonus','referral_referrer_bonus','payment_reversal',"
        "'referral_reversal','manual_grant')",
    )
    op.create_check_constraint(
        "ck_entitlement_entries_shape",
        "entitlement_entries",
        "(entry_type IN ('payment_grant','account_purchase_grant',"
        "'referral_user_bonus','referral_referrer_bonus','manual_grant') "
        "AND days_delta > 0 AND reversed_entry_id IS NULL) OR "
        "(entry_type IN ('payment_reversal','referral_reversal') "
        "AND days_delta < 0 AND reversed_entry_id IS NOT NULL)",
    )
    op.drop_column("entitlement_entries", "hours_delta")
