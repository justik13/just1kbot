"""phase 6 tariff-change payment intent invariants

Revision ID: 5e8a1c7d2f60
Revises: 91c4f2ab7d30
"""
from alembic import op
import sqlalchemy as sa

revision = "5e8a1c7d2f60"
down_revision = "91c4f2ab7d30"
branch_labels = depends_on = None


def upgrade():
    op.add_column("payments", sa.Column("provider_required", sa.Boolean(), server_default=sa.text("true"), nullable=False))
    op.create_index("uq_tariff_quotes_payment_id", "tariff_quotes", ["payment_id"], unique=True,
                    postgresql_where=sa.text("payment_id IS NOT NULL"))
    op.create_index("uq_payment_provider_create", "payment_provider_operations", ["payment_id"], unique=True,
                    postgresql_where=sa.text("operation_type='create_payment'"))
    statements = r"""
    CREATE FUNCTION phase6_validate_change_payment() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE q tariff_quotes%ROWTYPE; p payments%ROWTYPE; v tariff_versions%ROWTYPE;
    BEGIN
      IF TG_TABLE_NAME='payments' THEN
        p := NEW;
        IF p.tariff_quote_id IS NULL THEN RETURN NULL; END IF;
        SELECT * INTO q FROM tariff_quotes WHERE id=p.tariff_quote_id;
        IF NOT FOUND OR q.operation_type<>'change' THEN RETURN NULL; END IF;
      ELSE
        q := NEW;
        IF q.operation_type<>'change' OR q.payment_id IS NULL THEN RETURN NULL; END IF;
        SELECT * INTO p FROM payments WHERE id=q.payment_id;
      END IF;
      SELECT * INTO v FROM tariff_versions WHERE id=q.target_tariff_version_id;
      IF p.id IS NULL OR v.id IS NULL OR q.payment_id<>p.id OR p.tariff_quote_id<>q.id
         OR p.user_id<>q.user_id OR p.tariff_version_id<>q.target_tariff_version_id
         OR p.tariff_id<>v.tariff_id OR p.amount<>q.confirmed_payment_required_rub
         OR p.snapshot_amount<>q.confirmed_payment_required_rub
         OR p.currency<>q.currency OR p.snapshot_currency<>q.currency
         OR q.currency<>'RUB' OR p.provider_required<>(p.amount>0)
         OR (p.provider_required AND (p.provider_idempotency_key IS NULL OR NOT EXISTS
             (SELECT 1 FROM payment_provider_operations o WHERE o.payment_id=p.id AND o.operation_type='create_payment'
              AND o.idempotency_key=p.provider_idempotency_key)))
         OR (NOT p.provider_required AND (p.provider_idempotency_key IS NOT NULL OR p.external_id IS NOT NULL
             OR p.payment_url IS NOT NULL OR p.paid_at IS NOT NULL OR p.provider_confirmed_at IS NOT NULL
             OR EXISTS (SELECT 1 FROM payment_provider_operations o WHERE o.payment_id=p.id AND o.operation_type='create_payment')))
      THEN RAISE EXCEPTION 'invalid reciprocal tariff change payment identity' USING ERRCODE='23514'; END IF;
      RETURN NULL;
    END $$;
    -- PHASE6_SPLIT
    CREATE CONSTRAINT TRIGGER phase6_payment_reciprocal AFTER INSERT OR UPDATE ON payments
      DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION phase6_validate_change_payment();
    -- PHASE6_SPLIT
    CREATE CONSTRAINT TRIGGER phase6_quote_reciprocal AFTER INSERT OR UPDATE ON tariff_quotes
      DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION phase6_validate_change_payment();

    -- PHASE6_SPLIT
    CREATE FUNCTION phase6_immutable_financial_identity() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF OLD.tariff_quote_id IS NOT NULL AND EXISTS(SELECT 1 FROM tariff_quotes q WHERE q.id=OLD.tariff_quote_id AND q.operation_type='change')
         AND ROW(NEW.user_id,NEW.tariff_quote_id,NEW.tariff_version_id,NEW.tariff_id,NEW.amount,NEW.currency,
                 NEW.snapshot_amount,NEW.snapshot_currency,NEW.public_order_id,NEW.provider_idempotency_key,NEW.provider_required)
             IS DISTINCT FROM
             ROW(OLD.user_id,OLD.tariff_quote_id,OLD.tariff_version_id,OLD.tariff_id,OLD.amount,OLD.currency,
                 OLD.snapshot_amount,OLD.snapshot_currency,OLD.public_order_id,OLD.provider_idempotency_key,OLD.provider_required)
      THEN RAISE EXCEPTION 'immutable tariff change payment identity' USING ERRCODE='23514'; END IF;
      RETURN NEW;
    END $$;
    -- PHASE6_SPLIT
    CREATE TRIGGER phase6_payment_immutable BEFORE UPDATE ON payments FOR EACH ROW EXECUTE FUNCTION phase6_immutable_financial_identity();

    -- PHASE6_SPLIT
    CREATE FUNCTION phase6_quote_payment_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF OLD.operation_type='change' AND OLD.payment_id IS NOT NULL AND NEW.payment_id IS DISTINCT FROM OLD.payment_id
      THEN RAISE EXCEPTION 'immutable tariff quote payment link' USING ERRCODE='23514'; END IF;
      RETURN NEW;
    END $$;
    -- PHASE6_SPLIT
    CREATE TRIGGER phase6_quote_payment_immutable BEFORE UPDATE ON tariff_quotes FOR EACH ROW EXECUTE FUNCTION phase6_quote_payment_immutable();

    -- PHASE6_SPLIT
    CREATE FUNCTION phase6_provider_create_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF OLD.operation_type='create_payment' AND
         ROW(NEW.payment_id,NEW.operation_type,NEW.idempotency_key,NEW.payload)
         IS DISTINCT FROM ROW(OLD.payment_id,OLD.operation_type,OLD.idempotency_key,OLD.payload)
      THEN RAISE EXCEPTION 'immutable create-payment command' USING ERRCODE='23514'; END IF;
      RETURN NEW;
    END $$;
    -- PHASE6_SPLIT
    CREATE TRIGGER phase6_provider_create_immutable BEFORE UPDATE ON payment_provider_operations
      FOR EACH ROW EXECUTE FUNCTION phase6_provider_create_immutable();
    """
    for statement in statements.split("-- PHASE6_SPLIT"):
        op.execute(statement)


def downgrade():
    for statement in (
        "DROP TRIGGER IF EXISTS phase6_provider_create_immutable ON payment_provider_operations", "DROP FUNCTION IF EXISTS phase6_provider_create_immutable()",
        "DROP TRIGGER IF EXISTS phase6_quote_payment_immutable ON tariff_quotes", "DROP FUNCTION IF EXISTS phase6_quote_payment_immutable()",
        "DROP TRIGGER IF EXISTS phase6_payment_immutable ON payments", "DROP FUNCTION IF EXISTS phase6_immutable_financial_identity()",
        "DROP TRIGGER IF EXISTS phase6_quote_reciprocal ON tariff_quotes", "DROP TRIGGER IF EXISTS phase6_payment_reciprocal ON payments", "DROP FUNCTION IF EXISTS phase6_validate_change_payment()",
    ): op.execute(statement)
    op.drop_index("uq_payment_provider_create", table_name="payment_provider_operations")
    op.drop_index("uq_tariff_quotes_payment_id", table_name="tariff_quotes")
    op.drop_column("payments", "provider_required")
