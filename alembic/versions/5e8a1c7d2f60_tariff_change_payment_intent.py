"""Phase 6 tariff-change payment intent invariants."""
from alembic import op
import sqlalchemy as sa

revision = "5e8a1c7d2f60"
down_revision = "91c4f2ab7d30"
branch_labels = depends_on = None


def _exec_many(sql: str) -> None:
    for statement in sql.split("-- PHASE6_SPLIT"):
        op.execute(statement)


def upgrade():
    # Re-upgrade after downgrade restores semantics from immutable quote amount.
    op.add_column("payments", sa.Column("provider_required", sa.Boolean(), nullable=True))
    op.execute("UPDATE payments p SET provider_required = CASE WHEN q.operation_type='change' THEN q.confirmed_payment_required_rub > 0 ELSE true END FROM tariff_quotes q WHERE q.id=p.tariff_quote_id")
    op.execute("UPDATE payments SET provider_required=true WHERE provider_required IS NULL")
    op.alter_column("payments", "provider_required", nullable=False, server_default=sa.text("true"))

    # Refuse ambiguous history before installing constraints; never repair it.
    op.execute(r"""
    DO $$ BEGIN
      IF EXISTS (
        SELECT 1 FROM tariff_quotes q JOIN payments p ON p.id=q.payment_id
        LEFT JOIN tariff_versions v ON v.id=q.target_tariff_version_id
        WHERE q.operation_type='change' AND (
          p.tariff_quote_id IS DISTINCT FROM q.id OR p.user_id IS DISTINCT FROM q.user_id OR
          p.tariff_version_id IS DISTINCT FROM q.target_tariff_version_id OR p.tariff_id IS DISTINCT FROM v.tariff_id OR
          p.amount IS DISTINCT FROM q.confirmed_payment_required_rub OR p.snapshot_amount IS DISTINCT FROM q.confirmed_payment_required_rub OR
          p.currency IS DISTINCT FROM q.currency OR p.snapshot_currency IS DISTINCT FROM q.currency OR
          nullif(btrim(p.public_order_id),'') IS NULL OR p.snapshot_duration_days IS NOT NULL OR p.snapshot_device_limit IS NOT NULL OR
          p.amount::text IN ('NaN','Infinity','-Infinity') OR p.amount<0 OR p.provider_required IS DISTINCT FROM (p.amount>0) OR
          (p.amount>0 AND nullif(btrim(p.provider_idempotency_key),'') IS NULL) OR
          (p.amount>0 AND (SELECT count(*) FROM payment_provider_operations o WHERE o.payment_id=p.id AND o.operation_type='create_payment')<>1) OR
          (p.amount>0 AND EXISTS (SELECT 1 FROM payment_provider_operations o WHERE o.payment_id=p.id AND o.operation_type='create_payment' AND
             (o.idempotency_key IS DISTINCT FROM p.provider_idempotency_key OR o.payload#>>'{amount,value}' IS DISTINCT FROM to_char(p.amount,'FM9999999990.00') OR
              o.payload#>>'{amount,currency}' IS DISTINCT FROM p.currency OR o.payload->>'capture' IS DISTINCT FROM 'true' OR
              o.payload#>>'{metadata,order_id}' IS DISTINCT FROM p.public_order_id OR o.payload#>>'{metadata,local_payment_id}' IS DISTINCT FROM p.id::text OR
              (SELECT count(*) FROM jsonb_object_keys(o.payload))<>5 OR NOT (o.payload ?& ARRAY['amount','description','confirmation','metadata','capture']) OR
              (SELECT count(*) FROM jsonb_object_keys(o.payload->'amount'))<>2 OR NOT (o.payload->'amount' ?& ARRAY['value','currency']) OR
              (SELECT count(*) FROM jsonb_object_keys(o.payload->'metadata'))<>2 OR NOT (o.payload->'metadata' ?& ARRAY['order_id','local_payment_id']) OR
              (SELECT count(*) FROM jsonb_object_keys(o.payload->'confirmation'))<>2 OR NOT (o.payload->'confirmation' ?& ARRAY['type','return_url']) OR
              o.payload#>>'{confirmation,type}' IS DISTINCT FROM 'redirect' OR nullif(o.payload->>'description','') IS NULL OR nullif(o.payload#>>'{confirmation,return_url}','') IS NULL))) OR
          (p.amount=0 AND ((SELECT count(*) FROM payment_provider_operations o WHERE o.payment_id=p.id)<>0 OR
             p.provider_idempotency_key IS NOT NULL OR p.provider_status IS DISTINCT FROM 'not_created' OR p.external_id IS NOT NULL OR p.payment_url IS NOT NULL OR p.paid_at IS NOT NULL OR p.provider_confirmed_at IS NOT NULL))))
        OR EXISTS (SELECT 1 FROM payments p JOIN tariff_quotes q ON q.id=p.tariff_quote_id WHERE q.operation_type='change' AND q.payment_id IS DISTINCT FROM p.id)
      THEN RAISE EXCEPTION 'conflicting legacy tariff change payment rows'; END IF;
    END $$
    """)
    op.create_index("uq_tariff_quotes_payment_id", "tariff_quotes", ["payment_id"], unique=True, postgresql_where=sa.text("payment_id IS NOT NULL"))
    op.create_index("uq_payment_provider_create", "payment_provider_operations", ["payment_id"], unique=True, postgresql_where=sa.text("operation_type='create_payment'"))

    _exec_many(r"""
    CREATE FUNCTION phase6_validate_change_payment() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE q tariff_quotes%ROWTYPE; p payments%ROWTYPE; v tariff_versions%ROWTYPE; n integer; create_op payment_provider_operations%ROWTYPE;
    BEGIN
      IF TG_TABLE_NAME='payments' THEN p:=NEW; IF p.tariff_quote_id IS NULL THEN RETURN NULL; END IF; SELECT * INTO q FROM tariff_quotes WHERE id=p.tariff_quote_id;
      ELSE q:=NEW; IF q.payment_id IS NULL THEN RETURN NULL; END IF; SELECT * INTO p FROM payments WHERE id=q.payment_id; END IF;
      IF q.operation_type IS DISTINCT FROM 'change' THEN RETURN NULL; END IF;
      SELECT * INTO v FROM tariff_versions WHERE id=q.target_tariff_version_id;
      SELECT count(*) INTO n FROM payment_provider_operations o WHERE o.payment_id=p.id AND o.operation_type='create_payment';
      IF n=1 THEN SELECT * INTO create_op FROM payment_provider_operations x WHERE x.payment_id=p.id AND x.operation_type='create_payment'; END IF;
      IF p.id IS NULL OR v.id IS NULL OR q.payment_id IS DISTINCT FROM p.id OR p.tariff_quote_id IS DISTINCT FROM q.id
         OR p.user_id IS DISTINCT FROM q.user_id OR p.tariff_version_id IS DISTINCT FROM q.target_tariff_version_id
         OR p.tariff_id IS DISTINCT FROM v.tariff_id OR p.amount IS DISTINCT FROM q.confirmed_payment_required_rub
         OR p.snapshot_amount IS DISTINCT FROM q.confirmed_payment_required_rub OR p.currency IS DISTINCT FROM q.currency
         OR p.snapshot_currency IS DISTINCT FROM q.currency OR q.currency IS DISTINCT FROM 'RUB' OR nullif(btrim(p.public_order_id),'') IS NULL
         OR p.snapshot_duration_days IS NOT NULL OR p.snapshot_device_limit IS NOT NULL
         OR p.amount::text IN ('NaN','Infinity','-Infinity') OR p.amount<0
         OR p.provider_required IS DISTINCT FROM (p.amount>0) OR (p.provider_required AND (nullif(btrim(p.provider_idempotency_key),'') IS NULL OR n<>1
             OR create_op.idempotency_key IS DISTINCT FROM p.provider_idempotency_key OR create_op.payload#>>'{amount,value}' IS DISTINCT FROM to_char(p.amount,'FM9999999990.00')
             OR create_op.payload#>>'{amount,currency}' IS DISTINCT FROM p.currency OR create_op.payload->>'capture' IS DISTINCT FROM 'true'
             OR create_op.payload#>>'{metadata,order_id}' IS DISTINCT FROM p.public_order_id OR create_op.payload#>>'{metadata,local_payment_id}' IS DISTINCT FROM p.id::text
             OR (SELECT count(*) FROM jsonb_object_keys(create_op.payload))<>5 OR NOT (create_op.payload ?& ARRAY['amount','description','confirmation','metadata','capture'])
             OR (SELECT count(*) FROM jsonb_object_keys(create_op.payload->'amount'))<>2 OR NOT (create_op.payload->'amount' ?& ARRAY['value','currency'])
             OR (SELECT count(*) FROM jsonb_object_keys(create_op.payload->'metadata'))<>2 OR NOT (create_op.payload->'metadata' ?& ARRAY['order_id','local_payment_id'])
             OR (SELECT count(*) FROM jsonb_object_keys(create_op.payload->'confirmation'))<>2 OR NOT (create_op.payload->'confirmation' ?& ARRAY['type','return_url'])
             OR create_op.payload#>>'{confirmation,type}' IS DISTINCT FROM 'redirect' OR nullif(create_op.payload->>'description','') IS NULL OR nullif(create_op.payload#>>'{confirmation,return_url}','') IS NULL))
         OR (NOT p.provider_required AND (p.provider_idempotency_key IS NOT NULL OR (SELECT count(*) FROM payment_provider_operations z WHERE z.payment_id=p.id)<>0 OR p.external_id IS NOT NULL OR p.payment_url IS NOT NULL
             OR p.paid_at IS NOT NULL OR p.provider_confirmed_at IS NOT NULL OR p.provider_status IS DISTINCT FROM 'not_created'))
      THEN RAISE EXCEPTION 'invalid reciprocal tariff change payment identity' USING ERRCODE='23514'; END IF;
      RETURN NULL;
    END $$;
    -- PHASE6_SPLIT
    CREATE CONSTRAINT TRIGGER phase6_payment_reciprocal AFTER INSERT OR UPDATE ON payments DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION phase6_validate_change_payment();
    -- PHASE6_SPLIT
    CREATE CONSTRAINT TRIGGER phase6_quote_reciprocal AFTER INSERT OR UPDATE ON tariff_quotes DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION phase6_validate_change_payment();
    -- PHASE6_SPLIT
    CREATE FUNCTION phase6_immutable_financial_identity() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
      IF OLD.tariff_quote_id IS NOT NULL AND EXISTS(SELECT 1 FROM tariff_quotes q WHERE q.id=OLD.tariff_quote_id AND q.operation_type='change') AND
         ROW(NEW.user_id,NEW.tariff_quote_id,NEW.tariff_version_id,NEW.tariff_id,NEW.amount,NEW.currency,NEW.snapshot_amount,NEW.snapshot_currency,NEW.snapshot_duration_days,NEW.snapshot_device_limit,NEW.public_order_id,NEW.provider_idempotency_key,NEW.provider_required)
         IS DISTINCT FROM ROW(OLD.user_id,OLD.tariff_quote_id,OLD.tariff_version_id,OLD.tariff_id,OLD.amount,OLD.currency,OLD.snapshot_amount,OLD.snapshot_currency,OLD.snapshot_duration_days,OLD.snapshot_device_limit,OLD.public_order_id,OLD.provider_idempotency_key,OLD.provider_required)
      THEN RAISE EXCEPTION 'immutable tariff change payment identity' USING ERRCODE='23514'; END IF; RETURN NEW; END $$;
    -- PHASE6_SPLIT
    CREATE TRIGGER phase6_payment_immutable BEFORE UPDATE ON payments FOR EACH ROW EXECUTE FUNCTION phase6_immutable_financial_identity();
    -- PHASE6_SPLIT
    CREATE FUNCTION phase6_quote_payment_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
      IF OLD.operation_type='change' AND OLD.payment_id IS NOT NULL AND NEW.payment_id IS DISTINCT FROM OLD.payment_id THEN RAISE EXCEPTION 'immutable tariff quote payment link' USING ERRCODE='23514'; END IF; RETURN NEW; END $$;
    -- PHASE6_SPLIT
    CREATE TRIGGER phase6_quote_payment_immutable BEFORE UPDATE ON tariff_quotes FOR EACH ROW EXECUTE FUNCTION phase6_quote_payment_immutable();
    -- PHASE6_SPLIT
    CREATE FUNCTION phase6_provider_create_guard() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE p payments%ROWTYPE; q tariff_quotes%ROWTYPE; old_change boolean:=false; new_change boolean:=false;
    BEGIN
      IF TG_OP<>'INSERT' THEN old_change:=EXISTS(SELECT 1 FROM payments x JOIN tariff_quotes y ON y.id=x.tariff_quote_id WHERE x.id=OLD.payment_id AND y.operation_type='change'); END IF;
      IF TG_OP<>'DELETE' THEN new_change:=EXISTS(SELECT 1 FROM payments x JOIN tariff_quotes y ON y.id=x.tariff_quote_id WHERE x.id=NEW.payment_id AND y.operation_type='change'); END IF;
      IF NOT old_change AND NOT new_change THEN RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END; END IF;
      IF TG_OP='DELETE' THEN SELECT * INTO p FROM payments WHERE id=OLD.payment_id; ELSE SELECT * INTO p FROM payments WHERE id=NEW.payment_id; END IF;
      IF TG_OP='DELETE' AND OLD.operation_type='create_payment' THEN RAISE EXCEPTION 'durable create command cannot be deleted' USING ERRCODE='23514'; END IF;
      IF TG_OP='UPDATE' AND ROW(NEW.payment_id,NEW.operation_type,NEW.idempotency_key,NEW.payload) IS DISTINCT FROM ROW(OLD.payment_id,OLD.operation_type,OLD.idempotency_key,OLD.payload) THEN RAISE EXCEPTION 'immutable change payment provider command' USING ERRCODE='23514'; END IF;
      IF NOT p.provider_required THEN RAISE EXCEPTION 'zero change payment forbids provider operations' USING ERRCODE='23514'; END IF;
      IF NEW.operation_type='create_payment' AND (NOT p.provider_required OR NEW.idempotency_key IS DISTINCT FROM p.provider_idempotency_key OR
         NEW.payload#>>'{amount,value}' IS DISTINCT FROM to_char(p.amount,'FM9999999990.00') OR NEW.payload#>>'{amount,currency}' IS DISTINCT FROM p.currency OR
         NEW.payload#>>'{metadata,order_id}' IS DISTINCT FROM p.public_order_id OR NEW.payload#>>'{metadata,local_payment_id}' IS DISTINCT FROM p.id::text OR
         NEW.payload->>'capture' IS DISTINCT FROM 'true' OR (SELECT count(*) FROM jsonb_object_keys(NEW.payload))<>5 OR
         NOT (NEW.payload ?& ARRAY['amount','description','confirmation','metadata','capture']) OR
         (SELECT count(*) FROM jsonb_object_keys(NEW.payload->'amount'))<>2 OR NOT (NEW.payload->'amount' ?& ARRAY['value','currency']) OR
         (SELECT count(*) FROM jsonb_object_keys(NEW.payload->'metadata'))<>2 OR NOT (NEW.payload->'metadata' ?& ARRAY['order_id','local_payment_id']) OR
         (SELECT count(*) FROM jsonb_object_keys(NEW.payload->'confirmation'))<>2 OR NOT (NEW.payload->'confirmation' ?& ARRAY['type','return_url']) OR
         NEW.payload#>>'{confirmation,type}' IS DISTINCT FROM 'redirect' OR nullif(NEW.payload->>'description','') IS NULL OR
         nullif(NEW.payload#>>'{confirmation,return_url}','') IS NULL) THEN RAISE EXCEPTION 'invalid create-payment payload' USING ERRCODE='23514'; END IF;
      RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;
    END $$;
    -- PHASE6_SPLIT
    CREATE TRIGGER phase6_provider_create_guard BEFORE INSERT OR UPDATE OR DELETE ON payment_provider_operations FOR EACH ROW EXECUTE FUNCTION phase6_provider_create_guard();
    """)


def downgrade():
    for statement in (
        "DROP TRIGGER IF EXISTS phase6_provider_create_guard ON payment_provider_operations", "DROP FUNCTION IF EXISTS phase6_provider_create_guard()",
        "DROP TRIGGER IF EXISTS phase6_quote_payment_immutable ON tariff_quotes", "DROP FUNCTION IF EXISTS phase6_quote_payment_immutable()",
        "DROP TRIGGER IF EXISTS phase6_payment_immutable ON payments", "DROP FUNCTION IF EXISTS phase6_immutable_financial_identity()",
        "DROP TRIGGER IF EXISTS phase6_quote_reciprocal ON tariff_quotes", "DROP TRIGGER IF EXISTS phase6_payment_reciprocal ON payments", "DROP FUNCTION IF EXISTS phase6_validate_change_payment()",
    ): op.execute(statement)
    op.drop_index("uq_payment_provider_create", table_name="payment_provider_operations")
    op.drop_index("uq_tariff_quotes_payment_id", table_name="tariff_quotes")
    op.drop_column("payments", "provider_required")
