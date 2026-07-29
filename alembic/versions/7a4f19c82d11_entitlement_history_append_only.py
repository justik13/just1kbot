"""Protect entitlement history and add deterministic replay index.

Revision ID: 7a4f19c82d11
Revises: 3c7d9a21f001
"""
from alembic import op
import sqlalchemy as sa

revision = "7a4f19c82d11"
down_revision = "3c7d9a21f001"
branch_labels = depends_on = None


def upgrade():
    op.create_index("ix_entitlement_entries_user_history", "entitlement_entries",
                    ["beneficiary_user_id", "created_at", "id"])
    op.create_check_constraint(
        "ck_entitlement_entries_shape", "entitlement_entries",
        "(entry_type IN ('payment_grant','referral_user_bonus','referral_referrer_bonus','manual_grant') "
        "AND days_delta > 0 AND reversed_entry_id IS NULL) OR "
        "(entry_type IN ('payment_reversal','referral_reversal') "
        "AND days_delta < 0 AND reversed_entry_id IS NOT NULL)",
    )
    op.execute("""CREATE FUNCTION reject_entitlement_history_change() RETURNS trigger
    LANGUAGE plpgsql AS $$ BEGIN
      RAISE EXCEPTION 'entitlement entries are append-only';
    END $$""")
    op.execute("""CREATE TRIGGER entitlement_entries_append_only
    BEFORE UPDATE OR DELETE ON entitlement_entries FOR EACH ROW
    EXECUTE FUNCTION reject_entitlement_history_change()""")


def downgrade():
    op.drop_constraint("ck_entitlement_entries_shape", "entitlement_entries", type_="check")
    op.execute("DROP TRIGGER entitlement_entries_append_only ON entitlement_entries")
    op.execute("DROP FUNCTION reject_entitlement_history_change()")
    op.drop_index("ix_entitlement_entries_user_history", table_name="entitlement_entries")
