"""Freeze tracked source balance on tariff change quotes.

Revision ID: 91c4f2ab7d30
Revises: 7a4f19c82d11
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "91c4f2ab7d30"
down_revision = "7a4f19c82d11"
branch_labels = depends_on = None

_OLD_FUNCTION = """CREATE FUNCTION reject_quote_economic_change() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
  IF TG_OP='DELETE' OR ROW(NEW.public_id,NEW.user_id,NEW.operation_type,NEW.source_tariff_version_id,NEW.target_tariff_version_id,NEW.current_paid_hours,NEW.current_paid_value_rub,NEW.bonus_hours,NEW.confirmed_payment_required_rub,NEW.resulting_paid_hours,NEW.resulting_paid_value_rub,NEW.resulting_bonus_hours,NEW.rounding_loss_hours,NEW.rounding_loss_value_rub,NEW.currency,NEW.expires_at,NEW.created_at) IS DISTINCT FROM ROW(OLD.public_id,OLD.user_id,OLD.operation_type,OLD.source_tariff_version_id,OLD.target_tariff_version_id,OLD.current_paid_hours,OLD.current_paid_value_rub,OLD.bonus_hours,OLD.confirmed_payment_required_rub,OLD.resulting_paid_hours,OLD.resulting_paid_value_rub,OLD.resulting_bonus_hours,OLD.rounding_loss_hours,OLD.rounding_loss_value_rub,OLD.currency,OLD.expires_at,OLD.created_at) THEN RAISE EXCEPTION 'quote economic fields are immutable'; END IF; RETURN COALESCE(NEW,OLD); END $$"""

def upgrade():
    op.add_column("tariff_quotes", sa.Column("balance_as_of", sa.DateTime(timezone=True)))
    op.add_column("tariff_quotes", sa.Column("source_subscription_end", sa.DateTime(timezone=True)))
    op.add_column("tariff_quotes", sa.Column("source_balance_fingerprint", sa.String(64)))
    op.add_column("tariff_quotes", sa.Column("source_entitlement_entry_ids", postgresql.JSONB()))
    op.add_column("tariff_quotes", sa.Column("source_ledger_entry_ids", postgresql.JSONB()))
    op.create_check_constraint("ck_tariff_quotes_change_source_snapshot", "tariff_quotes", "operation_type <> 'change' OR (source_tariff_version_id IS NOT NULL AND target_tariff_version_id IS NOT NULL AND source_tariff_version_id <> target_tariff_version_id AND balance_as_of IS NOT NULL AND source_subscription_end IS NOT NULL AND source_balance_fingerprint IS NOT NULL AND source_entitlement_entry_ids IS NOT NULL AND source_ledger_entry_ids IS NOT NULL)")
    op.create_check_constraint("ck_tariff_quotes_fingerprint", "tariff_quotes", "source_balance_fingerprint IS NULL OR source_balance_fingerprint ~ '^[0-9a-f]{64}$'")
    op.execute("""CREATE FUNCTION is_nonnegative_integer_json_array(value jsonb) RETURNS boolean LANGUAGE sql IMMUTABLE AS $$
      SELECT value IS NOT NULL AND jsonb_typeof(value)='array' AND NOT EXISTS (
        SELECT 1 FROM jsonb_array_elements(value) item
        WHERE jsonb_typeof(item) <> 'number' OR (item::text)::numeric < 0
          OR trunc((item::text)::numeric) <> (item::text)::numeric)
      ) $$""")
    op.create_check_constraint("ck_tariff_quotes_source_arrays", "tariff_quotes", "(source_entitlement_entry_ids IS NULL OR is_nonnegative_integer_json_array(source_entitlement_entry_ids)) AND (source_ledger_entry_ids IS NULL OR is_nonnegative_integer_json_array(source_ledger_entry_ids))")
    op.create_check_constraint("ck_tariff_quotes_lifecycle_timestamps", "tariff_quotes", "(status = 'consumed') = (consumed_at IS NOT NULL) AND (status = 'manual_review') = (manual_review_at IS NOT NULL)")
    op.execute("DROP TRIGGER tariff_quotes_immutable ON tariff_quotes")
    op.execute("DROP FUNCTION reject_quote_economic_change()")
    op.execute("""CREATE FUNCTION reject_quote_economic_change() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
      IF TG_OP='DELETE' THEN RAISE EXCEPTION 'quote deletion is forbidden'; END IF;
      IF ROW(NEW.public_id,NEW.user_id,NEW.operation_type,NEW.source_tariff_version_id,NEW.target_tariff_version_id,NEW.current_paid_hours,NEW.current_paid_value_rub,NEW.bonus_hours,NEW.confirmed_payment_required_rub,NEW.resulting_paid_hours,NEW.resulting_paid_value_rub,NEW.resulting_bonus_hours,NEW.rounding_loss_hours,NEW.rounding_loss_value_rub,NEW.currency,NEW.expires_at,NEW.created_at,NEW.balance_as_of,NEW.source_subscription_end,NEW.source_balance_fingerprint,NEW.source_entitlement_entry_ids,NEW.source_ledger_entry_ids) IS DISTINCT FROM ROW(OLD.public_id,OLD.user_id,OLD.operation_type,OLD.source_tariff_version_id,OLD.target_tariff_version_id,OLD.current_paid_hours,OLD.current_paid_value_rub,OLD.bonus_hours,OLD.confirmed_payment_required_rub,OLD.resulting_paid_hours,OLD.resulting_paid_value_rub,OLD.resulting_bonus_hours,OLD.rounding_loss_hours,OLD.rounding_loss_value_rub,OLD.currency,OLD.expires_at,OLD.created_at,OLD.balance_as_of,OLD.source_subscription_end,OLD.source_balance_fingerprint,OLD.source_entitlement_entry_ids,OLD.source_ledger_entry_ids) THEN RAISE EXCEPTION 'quote economic fields are immutable'; END IF;
      IF NEW.status <> OLD.status AND NOT ((OLD.status='active' AND NEW.status IN ('expired','cancelled','consumed','manual_review')) OR (OLD.status='expired' AND NEW.status IN ('consumed','manual_review')) OR (OLD.status='cancelled' AND NEW.status='manual_review')) THEN RAISE EXCEPTION 'invalid quote lifecycle transition'; END IF;
      RETURN NEW; END $$""")
    op.execute("CREATE TRIGGER tariff_quotes_immutable BEFORE UPDATE OR DELETE ON tariff_quotes FOR EACH ROW EXECUTE FUNCTION reject_quote_economic_change()")

def downgrade():
    op.execute("DROP TRIGGER tariff_quotes_immutable ON tariff_quotes")
    op.execute("DROP FUNCTION reject_quote_economic_change()")
    op.execute(_OLD_FUNCTION)
    op.execute("CREATE TRIGGER tariff_quotes_immutable BEFORE UPDATE OR DELETE ON tariff_quotes FOR EACH ROW EXECUTE FUNCTION reject_quote_economic_change()")
    op.drop_constraint("ck_tariff_quotes_lifecycle_timestamps", "tariff_quotes", type_="check")
    op.drop_constraint("ck_tariff_quotes_source_arrays", "tariff_quotes", type_="check")
    op.execute("DROP FUNCTION is_nonnegative_integer_json_array(jsonb)")
    op.drop_constraint("ck_tariff_quotes_fingerprint", "tariff_quotes", type_="check")
    op.drop_constraint("ck_tariff_quotes_change_source_snapshot", "tariff_quotes", type_="check")
    for column in ("source_ledger_entry_ids", "source_entitlement_entry_ids", "source_balance_fingerprint", "source_subscription_end", "balance_as_of"):
        op.drop_column("tariff_quotes", column)
