"""Add quote-backed paid value, entitlement and referral sources.

Revision ID: 4f2a9d6c1e30
Revises: 3c8e4d1b7a20
"""

import sqlalchemy as sa
from alembic import op


revision = "4f2a9d6c1e30"
down_revision = "3c8e4d1b7a20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tariff_quotes", sa.Column("purchase_notified_at", sa.DateTime(timezone=True))
    )
    op.add_column(
        "tariff_quotes", sa.Column("referral_processed_at", sa.DateTime(timezone=True))
    )

    op.drop_constraint(
        "ck_paid_value_ledger_entry_type", "paid_value_ledger", type_="check"
    )
    op.drop_constraint(
        "ck_paid_value_confirmed_shape", "paid_value_ledger", type_="check"
    )
    op.create_check_constraint(
        "ck_paid_value_ledger_entry_type",
        "paid_value_ledger",
        "entry_type IN ('confirmed_payment','account_purchase',"
        "'tariff_conversion','payment_reversal','manual_adjustment')",
    )
    op.create_check_constraint(
        "ck_paid_value_confirmed_shape",
        "paid_value_ledger",
        "entry_type <> 'confirmed_payment' OR (payment_id IS NOT NULL "
        "AND quote_id IS NOT NULL AND reversal_of_id IS NULL "
        "AND paid_hours_delta > 0 AND paid_value_rub_delta > 0)",
    )
    op.create_check_constraint(
        "ck_paid_value_account_purchase_shape",
        "paid_value_ledger",
        "entry_type <> 'account_purchase' OR (payment_id IS NULL "
        "AND quote_id IS NOT NULL AND reversal_of_id IS NULL "
        "AND paid_hours_delta > 0 AND paid_value_rub_delta > 0)",
    )
    op.create_index(
        "uq_paid_value_account_purchase",
        "paid_value_ledger",
        ["quote_id"],
        unique=True,
        postgresql_where=sa.text("entry_type='account_purchase'"),
    )

    op.drop_constraint(
        "ck_entitlement_entries_type", "entitlement_entries", type_="check"
    )
    op.drop_constraint(
        "ck_entitlement_entries_shape", "entitlement_entries", type_="check"
    )
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

    op.alter_column("referral_rewards", "source_payment_id", nullable=True)
    op.add_column(
        "referral_rewards",
        sa.Column(
            "source_quote_id",
            sa.BigInteger(),
            sa.ForeignKey("tariff_quotes.id", ondelete="RESTRICT"),
        ),
    )
    op.create_index(
        "uq_referral_rewards_source_quote",
        "referral_rewards",
        ["source_quote_id"],
        unique=True,
        postgresql_where=sa.text("source_quote_id IS NOT NULL"),
    )
    op.create_check_constraint(
        "ck_referral_rewards_one_source",
        "referral_rewards",
        "(source_payment_id IS NOT NULL) <> (source_quote_id IS NOT NULL)",
    )
    op.add_column(
        "referral_eligibilities",
        sa.Column(
            "source_quote_id",
            sa.BigInteger(),
            sa.ForeignKey("tariff_quotes.id", ondelete="RESTRICT"),
        ),
    )
    op.create_check_constraint(
        "ck_referral_eligibilities_one_source",
        "referral_eligibilities",
        "source_payment_id IS NULL OR source_quote_id IS NULL",
    )


def downgrade() -> None:
    # These rows have no representation before this revision. Remove them before
    # restoring the older constraints. Application append-only triggers are
    # disabled only for this transactional rollback and restored immediately.
    op.execute("DELETE FROM referral_rewards WHERE source_quote_id IS NOT NULL")
    op.execute(
        "UPDATE referral_eligibilities SET source_quote_id = NULL "
        "WHERE source_quote_id IS NOT NULL"
    )
    op.execute(
        "ALTER TABLE entitlement_entries "
        "DISABLE TRIGGER entitlement_entries_append_only"
    )
    op.execute(
        "DELETE FROM entitlement_entries "
        "WHERE entry_type = 'account_purchase_grant'"
    )
    op.execute(
        "ALTER TABLE entitlement_entries "
        "ENABLE TRIGGER entitlement_entries_append_only"
    )
    op.execute(
        "ALTER TABLE paid_value_ledger "
        "DISABLE TRIGGER paid_value_ledger_append_only"
    )
    op.execute(
        "DELETE FROM paid_value_ledger WHERE entry_type = 'account_purchase'"
    )
    op.execute(
        "ALTER TABLE paid_value_ledger "
        "ENABLE TRIGGER paid_value_ledger_append_only"
    )

    op.drop_constraint(
        "ck_referral_eligibilities_one_source",
        "referral_eligibilities",
        type_="check",
    )
    op.drop_column("referral_eligibilities", "source_quote_id")
    op.drop_constraint(
        "ck_referral_rewards_one_source", "referral_rewards", type_="check"
    )
    op.drop_index(
        "uq_referral_rewards_source_quote", table_name="referral_rewards"
    )
    op.drop_column("referral_rewards", "source_quote_id")
    op.alter_column("referral_rewards", "source_payment_id", nullable=False)

    op.drop_constraint(
        "ck_entitlement_entries_shape", "entitlement_entries", type_="check"
    )
    op.drop_constraint(
        "ck_entitlement_entries_type", "entitlement_entries", type_="check"
    )
    op.create_check_constraint(
        "ck_entitlement_entries_type",
        "entitlement_entries",
        "entry_type IN ('payment_grant','referral_user_bonus',"
        "'referral_referrer_bonus','payment_reversal','referral_reversal',"
        "'manual_grant')",
    )
    op.create_check_constraint(
        "ck_entitlement_entries_shape",
        "entitlement_entries",
        "(entry_type IN ('payment_grant','referral_user_bonus',"
        "'referral_referrer_bonus','manual_grant') AND days_delta > 0 "
        "AND reversed_entry_id IS NULL) OR "
        "(entry_type IN ('payment_reversal','referral_reversal') "
        "AND days_delta < 0 AND reversed_entry_id IS NOT NULL)",
    )

    op.drop_index("uq_paid_value_account_purchase", table_name="paid_value_ledger")
    op.drop_constraint(
        "ck_paid_value_account_purchase_shape", "paid_value_ledger", type_="check"
    )
    op.drop_constraint(
        "ck_paid_value_confirmed_shape", "paid_value_ledger", type_="check"
    )
    op.drop_constraint(
        "ck_paid_value_ledger_entry_type", "paid_value_ledger", type_="check"
    )
    op.create_check_constraint(
        "ck_paid_value_confirmed_shape",
        "paid_value_ledger",
        "entry_type <> 'confirmed_payment' OR (payment_id IS NOT NULL "
        "AND quote_id IS NOT NULL AND reversal_of_id IS NULL "
        "AND paid_hours_delta > 0 AND paid_value_rub_delta > 0)",
    )
    op.create_check_constraint(
        "ck_paid_value_ledger_entry_type",
        "paid_value_ledger",
        "entry_type IN ('confirmed_payment','tariff_conversion',"
        "'payment_reversal','manual_adjustment')",
    )
    op.drop_column("tariff_quotes", "referral_processed_at")
    op.drop_column("tariff_quotes", "purchase_notified_at")
