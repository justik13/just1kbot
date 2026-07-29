"""Add immutable tariff quotes and paid-value ledger foundation.

Revision ID: 3c7d9a21f001
Revises: c91da7b32f10
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "3c7d9a21f001"
down_revision = "c91da7b32f10"
branch_labels = depends_on = None


def upgrade():
    op.create_table("tariff_versions",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("tariff_id", sa.Integer(), sa.ForeignKey("tariffs.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("name_snapshot", sa.String(100), nullable=False),
        sa.Column("duration_hours", sa.Integer(), nullable=False),
        sa.Column("device_limit", sa.Integer(), nullable=False),
        sa.Column("price_rub", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="RUB"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tariff_id", "version_number", name="uq_tariff_versions_number"),
        sa.CheckConstraint("duration_hours > 0", name="ck_tariff_versions_duration_positive"),
        sa.CheckConstraint("device_limit > 0", name="ck_tariff_versions_device_limit_positive"),
        sa.CheckConstraint("price_rub > 0", name="ck_tariff_versions_price_positive"),
        sa.CheckConstraint("currency = 'RUB'", name="ck_tariff_versions_currency_rub"))
    op.create_index("ix_tariff_versions_tariff_id", "tariff_versions", ["tariff_id"])
    op.create_table("tariff_quotes",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("public_id", postgresql.UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("operation_type", sa.String(20), nullable=False),
        sa.Column("source_tariff_version_id", sa.BigInteger(), sa.ForeignKey("tariff_versions.id", ondelete="RESTRICT")),
        sa.Column("target_tariff_version_id", sa.BigInteger(), sa.ForeignKey("tariff_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("current_paid_hours", sa.Integer(), nullable=False),
        sa.Column("current_paid_value_rub", sa.Numeric(18, 6), nullable=False),
        sa.Column("bonus_hours", sa.Integer(), nullable=False),
        sa.Column("confirmed_payment_required_rub", sa.Numeric(12, 2), nullable=False),
        sa.Column("resulting_paid_hours", sa.Integer(), nullable=False),
        sa.Column("resulting_paid_value_rub", sa.Numeric(18, 6), nullable=False),
        sa.Column("resulting_bonus_hours", sa.Integer(), nullable=False),
        sa.Column("rounding_loss_hours", sa.Numeric(18, 12), nullable=False),
        sa.Column("rounding_loss_value_rub", sa.Numeric(18, 6), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="RUB"),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column("manual_review_at", sa.DateTime(timezone=True)),
        sa.Column("diagnostic_reason", sa.String(255)),
        sa.Column("payment_id", sa.Integer()),
        sa.CheckConstraint("operation_type IN ('purchase','renew','change')", name="ck_tariff_quotes_operation"),
        sa.CheckConstraint("status IN ('active','consumed','expired','cancelled','manual_review')", name="ck_tariff_quotes_status"),
        sa.CheckConstraint("currency='RUB'", name="ck_tariff_quotes_currency_rub"),
        sa.CheckConstraint("current_paid_hours>=0 AND bonus_hours>=0 AND resulting_paid_hours>=0 AND resulting_bonus_hours>=0", name="ck_tariff_quotes_hours_nonnegative"),
        sa.CheckConstraint("current_paid_value_rub>=0 AND confirmed_payment_required_rub>=0 AND resulting_paid_value_rub>=0 AND rounding_loss_value_rub>=0", name="ck_tariff_quotes_values_nonnegative"),
        sa.CheckConstraint("rounding_loss_hours>=0 AND rounding_loss_hours<1", name="ck_tariff_quotes_rounding_loss"),
        sa.CheckConstraint("resulting_paid_value_rub <= current_paid_value_rub + confirmed_payment_required_rub", name="ck_tariff_quotes_value_invariant"),
        sa.CheckConstraint("expires_at = created_at + interval '15 minutes'", name="ck_tariff_quotes_lifetime"))
    op.create_index("ix_tariff_quotes_user_id", "tariff_quotes", ["user_id"])
    op.create_index("uq_tariff_quotes_active_change_user", "tariff_quotes", ["user_id"], unique=True, postgresql_where=sa.text("operation_type='change' AND status='active'"))
    op.create_index("uq_tariff_quotes_active_checkout", "tariff_quotes", ["user_id", "target_tariff_version_id", "operation_type"], unique=True, postgresql_where=sa.text("status='active' AND operation_type IN ('purchase','renew')"))
    # Nullable first: existing payments remain valid legacy snapshot orders.
    op.add_column("payments", sa.Column("tariff_quote_id", sa.BigInteger(), nullable=True))
    op.add_column("payments", sa.Column("tariff_version_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key("fk_payments_tariff_quote", "payments", "tariff_quotes", ["tariff_quote_id"], ["id"], ondelete="RESTRICT")
    op.create_foreign_key("fk_payments_tariff_version", "payments", "tariff_versions", ["tariff_version_id"], ["id"], ondelete="RESTRICT")
    op.create_index("uq_payments_tariff_quote", "payments", ["tariff_quote_id"], unique=True, postgresql_where=sa.text("tariff_quote_id IS NOT NULL"))
    op.create_foreign_key("fk_tariff_quotes_payment", "tariff_quotes", "payments", ["payment_id"], ["id"], ondelete="RESTRICT")
    op.create_table("paid_value_ledger",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("source_type", sa.String(40), nullable=False), sa.Column("source_id", sa.String(100), nullable=False),
        sa.Column("entry_type", sa.String(30), nullable=False), sa.Column("paid_hours_delta", sa.Integer(), nullable=False),
        sa.Column("paid_value_rub_delta", sa.Numeric(18, 6), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="RUB"),
        sa.Column("tariff_version_id", sa.BigInteger(), sa.ForeignKey("tariff_versions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("quote_id", sa.BigInteger(), sa.ForeignKey("tariff_quotes.id", ondelete="RESTRICT")),
        sa.Column("payment_id", sa.Integer(), sa.ForeignKey("payments.id", ondelete="RESTRICT")),
        sa.Column("reversal_of_id", sa.BigInteger(), sa.ForeignKey("paid_value_ledger.id", ondelete="RESTRICT")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.CheckConstraint("entry_type IN ('confirmed_payment','tariff_conversion','payment_reversal','manual_adjustment')", name="ck_paid_value_ledger_entry_type"),
        sa.CheckConstraint("currency='RUB'", name="ck_paid_value_ledger_currency_rub"))
    op.create_index("ix_paid_value_ledger_user_id", "paid_value_ledger", ["user_id"])
    op.create_index("uq_paid_value_confirmed_payment", "paid_value_ledger", ["payment_id"], unique=True, postgresql_where=sa.text("entry_type='confirmed_payment'"))
    op.create_index("uq_paid_value_conversion_quote", "paid_value_ledger", ["quote_id"], unique=True, postgresql_where=sa.text("entry_type='tariff_conversion'"))
    op.create_index("uq_paid_value_reversal", "paid_value_ledger", ["reversal_of_id"], unique=True, postgresql_where=sa.text("entry_type='payment_reversal'"))
    op.execute("""CREATE FUNCTION reject_tariff_version_history_change() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
      IF TG_OP='DELETE' OR ROW(NEW.tariff_id,NEW.version_number,NEW.name_snapshot,NEW.duration_hours,NEW.device_limit,NEW.price_rub,NEW.currency,NEW.created_at) IS DISTINCT FROM ROW(OLD.tariff_id,OLD.version_number,OLD.name_snapshot,OLD.duration_hours,OLD.device_limit,OLD.price_rub,OLD.currency,OLD.created_at) THEN
        IF EXISTS(SELECT 1 FROM tariff_quotes WHERE source_tariff_version_id=OLD.id OR target_tariff_version_id=OLD.id) OR EXISTS(SELECT 1 FROM payments WHERE tariff_version_id=OLD.id) THEN RAISE EXCEPTION 'used tariff version is immutable'; END IF;
      END IF; RETURN COALESCE(NEW,OLD); END $$""")
    op.execute("CREATE TRIGGER tariff_versions_immutable BEFORE UPDATE OR DELETE ON tariff_versions FOR EACH ROW EXECUTE FUNCTION reject_tariff_version_history_change()")
    op.execute("""CREATE FUNCTION reject_quote_economic_change() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
      IF TG_OP='DELETE' OR ROW(NEW.public_id,NEW.user_id,NEW.operation_type,NEW.source_tariff_version_id,NEW.target_tariff_version_id,NEW.current_paid_hours,NEW.current_paid_value_rub,NEW.bonus_hours,NEW.confirmed_payment_required_rub,NEW.resulting_paid_hours,NEW.resulting_paid_value_rub,NEW.resulting_bonus_hours,NEW.rounding_loss_hours,NEW.rounding_loss_value_rub,NEW.currency,NEW.expires_at,NEW.created_at) IS DISTINCT FROM ROW(OLD.public_id,OLD.user_id,OLD.operation_type,OLD.source_tariff_version_id,OLD.target_tariff_version_id,OLD.current_paid_hours,OLD.current_paid_value_rub,OLD.bonus_hours,OLD.confirmed_payment_required_rub,OLD.resulting_paid_hours,OLD.resulting_paid_value_rub,OLD.resulting_bonus_hours,OLD.rounding_loss_hours,OLD.rounding_loss_value_rub,OLD.currency,OLD.expires_at,OLD.created_at) THEN RAISE EXCEPTION 'quote economic fields are immutable'; END IF; RETURN COALESCE(NEW,OLD); END $$""")
    op.execute("CREATE TRIGGER tariff_quotes_immutable BEFORE UPDATE OR DELETE ON tariff_quotes FOR EACH ROW EXECUTE FUNCTION reject_quote_economic_change()")
    op.execute("""CREATE FUNCTION reject_ledger_change() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'paid-value ledger is append-only'; END $$""")
    op.execute("CREATE TRIGGER paid_value_ledger_append_only BEFORE UPDATE OR DELETE ON paid_value_ledger FOR EACH ROW EXECUTE FUNCTION reject_ledger_change()")


def downgrade():
    op.execute("DROP TRIGGER paid_value_ledger_append_only ON paid_value_ledger")
    op.execute("DROP FUNCTION reject_ledger_change()")
    op.execute("DROP TRIGGER tariff_quotes_immutable ON tariff_quotes")
    op.execute("DROP FUNCTION reject_quote_economic_change()")
    op.execute("DROP TRIGGER tariff_versions_immutable ON tariff_versions")
    op.execute("DROP FUNCTION reject_tariff_version_history_change()")
    op.drop_table("paid_value_ledger")
    op.drop_constraint("fk_tariff_quotes_payment", "tariff_quotes", type_="foreignkey")
    op.drop_index("uq_payments_tariff_quote", table_name="payments")
    op.drop_constraint("fk_payments_tariff_version", "payments", type_="foreignkey")
    op.drop_constraint("fk_payments_tariff_quote", "payments", type_="foreignkey")
    op.drop_column("payments", "tariff_version_id")
    op.drop_column("payments", "tariff_quote_id")
    op.drop_table("tariff_quotes")
    op.drop_table("tariff_versions")
