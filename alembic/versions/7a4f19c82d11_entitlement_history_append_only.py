"""Protect entitlement history and add deterministic replay index.

Revision ID: 7a4f19c82d11
Revises: 3c7d9a21f001
"""
from alembic import op

revision = "7a4f19c82d11"
down_revision = "3c7d9a21f001"
branch_labels = depends_on = None


def upgrade():
    op.create_index("ix_entitlement_entries_user_history", "entitlement_entries",
                    ["beneficiary_user_id", "created_at", "id"])
    op.execute("""CREATE FUNCTION reject_entitlement_history_change() RETURNS trigger
    LANGUAGE plpgsql AS $$ BEGIN
      RAISE EXCEPTION 'entitlement entries are append-only';
    END $$""")
    op.execute("""CREATE TRIGGER entitlement_entries_append_only
    BEFORE UPDATE OR DELETE ON entitlement_entries FOR EACH ROW
    EXECUTE FUNCTION reject_entitlement_history_change()""")


def downgrade():
    op.execute("DROP TRIGGER entitlement_entries_append_only ON entitlement_entries")
    op.execute("DROP FUNCTION reject_entitlement_history_change()")
    op.drop_index("ix_entitlement_entries_user_history", table_name="entitlement_entries")
