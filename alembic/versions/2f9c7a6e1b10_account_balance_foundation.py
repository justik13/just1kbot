"""Add the append-only real-money account foundation.

Revision ID: 2f9c7a6e1b10
Revises: 5e8a1c7d2f60
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "2f9c7a6e1b10"
down_revision = "5e8a1c7d2f60"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "financial_hold",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "topup_blocked",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "users", sa.Column("financial_block_reason", sa.String(255))
    )

    op.add_column(
        "payments",
        sa.Column(
            "payment_kind",
            sa.String(40),
            nullable=False,
            server_default="legacy_subscription_purchase",
        ),
    )
    op.add_column(
        "payments",
        sa.Column(
            "ui_visible",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.add_column(
        "payments",
        sa.Column(
            "topup_context",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "payments", sa.Column("credited_at", sa.DateTime(timezone=True))
    )
    op.add_column(
        "payments", sa.Column("credit_notified_at", sa.DateTime(timezone=True))
    )
    op.alter_column("payments", "tariff_id", nullable=True)
    op.create_check_constraint(
        "ck_payments_kind",
        "payments",
        "payment_kind IN ('legacy_subscription_purchase','balance_topup')",
    )
    op.create_check_constraint(
        "ck_payments_balance_topup_shape",
        "payments",
        "payment_kind <> 'balance_topup' OR "
        "(tariff_id IS NULL AND tariff_quote_id IS NULL "
        "AND tariff_version_id IS NULL AND snapshot_duration_days IS NULL "
        "AND snapshot_device_limit IS NULL)",
    )
    op.create_check_constraint(
        "ck_payments_balance_topup_money",
        "payments",
        "payment_kind <> 'balance_topup' OR "
        "(currency = 'RUB' AND amount > 0 AND amount = trunc(amount))",
    )
    op.create_index(
        "uq_payments_visible_balance_topup_user",
        "payments",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text(
            "payment_kind='balance_topup' AND ui_visible=true "
            "AND checkout_status='active' "
            "AND provider_status NOT IN ('succeeded','canceled','refunded')"
        ),
    )
    op.create_index(
        "ix_payments_balance_topup_created",
        "payments",
        ["user_id", "created_at"],
        postgresql_where=sa.text("payment_kind='balance_topup'"),
    )

    op.create_table(
        "account_ledger_entries",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("entry_type", sa.String(30), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="RUB"),
        sa.Column(
            "payment_id",
            sa.Integer(),
            sa.ForeignKey("payments.id", ondelete="RESTRICT"),
        ),
        sa.Column(
            "quote_id",
            sa.BigInteger(),
            sa.ForeignKey("tariff_quotes.id", ondelete="RESTRICT"),
        ),
        sa.Column("reversal_of_id", sa.BigInteger()),
        sa.Column("idempotency_key", sa.String(160), nullable=False, unique=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "entry_type IN ('payment_credit','purchase_debit',"
            "'purchase_reversal','refund_debit','chargeback_debit',"
            "'admin_adjustment')",
            name="ck_account_ledger_entry_type",
        ),
        sa.CheckConstraint(
            "currency = 'RUB'", name="ck_account_ledger_currency_rub"
        ),
        sa.CheckConstraint(
            "amount <> 0 AND amount = trunc(amount)",
            name="ck_account_ledger_whole_nonzero_amount",
        ),
        sa.CheckConstraint(
            "(entry_type = 'payment_credit' AND amount > 0 "
            "AND payment_id IS NOT NULL AND quote_id IS NULL "
            "AND reversal_of_id IS NULL) OR "
            "(entry_type = 'purchase_debit' AND amount < 0 "
            "AND payment_id IS NULL AND quote_id IS NOT NULL "
            "AND reversal_of_id IS NULL) OR "
            "(entry_type = 'purchase_reversal' AND amount > 0 "
            "AND payment_id IS NULL AND quote_id IS NOT NULL "
            "AND reversal_of_id IS NOT NULL) OR "
            "(entry_type IN ('refund_debit','chargeback_debit') "
            "AND amount < 0 AND payment_id IS NOT NULL "
            "AND quote_id IS NULL AND reversal_of_id IS NULL) OR "
            "(entry_type = 'admin_adjustment' AND payment_id IS NULL "
            "AND quote_id IS NULL AND reversal_of_id IS NULL)",
            name="ck_account_ledger_entry_shape",
        ),
    )
    op.create_foreign_key(
        "fk_account_ledger_reversal",
        "account_ledger_entries",
        "account_ledger_entries",
        ["reversal_of_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_account_ledger_entries_user_id",
        "account_ledger_entries",
        ["user_id"],
    )
    op.create_index(
        "ix_account_ledger_user_history",
        "account_ledger_entries",
        ["user_id", "created_at", "id"],
    )
    op.create_index(
        "uq_account_ledger_payment_credit",
        "account_ledger_entries",
        ["payment_id"],
        unique=True,
        postgresql_where=sa.text("entry_type='payment_credit'"),
    )
    op.create_index(
        "uq_account_ledger_purchase_debit",
        "account_ledger_entries",
        ["quote_id"],
        unique=True,
        postgresql_where=sa.text("entry_type='purchase_debit'"),
    )
    op.create_index(
        "uq_account_ledger_reversal",
        "account_ledger_entries",
        ["reversal_of_id"],
        unique=True,
        postgresql_where=sa.text("entry_type='purchase_reversal'"),
    )

    op.create_table(
        "account_ledger_allocations",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "credit_entry_id",
            sa.BigInteger(),
            sa.ForeignKey("account_ledger_entries.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "debit_entry_id",
            sa.BigInteger(),
            sa.ForeignKey("account_ledger_entries.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("idempotency_key", sa.String(180), nullable=False, unique=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "amount > 0 AND amount = trunc(amount)",
            name="ck_account_allocations_whole_positive_amount",
        ),
        sa.UniqueConstraint(
            "credit_entry_id",
            "debit_entry_id",
            name="uq_account_allocations_credit_debit",
        ),
    )
    op.create_index(
        "ix_account_ledger_allocations_user_id",
        "account_ledger_allocations",
        ["user_id"],
    )
    op.create_index(
        "ix_account_allocations_credit",
        "account_ledger_allocations",
        ["credit_entry_id", "id"],
    )
    op.create_index(
        "ix_account_allocations_debit",
        "account_ledger_allocations",
        ["debit_entry_id", "id"],
    )

    op.create_table(
        "account_balance_reservations",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "payment_id",
            sa.Integer(),
            sa.ForeignKey("payments.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("reservation_type", sa.String(20), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="RUB"),
        sa.Column(
            "status", sa.String(20), nullable=False, server_default="active"
        ),
        sa.Column("idempotency_key", sa.String(180), nullable=False, unique=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "reservation_type IN ('refund','dispute')",
            name="ck_account_reservations_type",
        ),
        sa.CheckConstraint(
            "status IN ('active','released','consumed')",
            name="ck_account_reservations_status",
        ),
        sa.CheckConstraint(
            "amount > 0 AND amount = trunc(amount)",
            name="ck_account_reservations_whole_positive_amount",
        ),
        sa.CheckConstraint(
            "currency = 'RUB'", name="ck_account_reservations_currency_rub"
        ),
        sa.CheckConstraint(
            "(status = 'active' AND resolved_at IS NULL) OR "
            "(status IN ('released','consumed') AND resolved_at IS NOT NULL)",
            name="ck_account_reservations_lifecycle",
        ),
    )
    op.create_index(
        "ix_account_balance_reservations_user_id",
        "account_balance_reservations",
        ["user_id"],
    )
    op.create_index(
        "ix_account_balance_reservations_payment_id",
        "account_balance_reservations",
        ["payment_id"],
    )
    op.create_index(
        "ix_account_reservations_active_user",
        "account_balance_reservations",
        ["user_id", "id"],
        postgresql_where=sa.text("status='active'"),
    )

    op.execute(
        """
        CREATE FUNCTION reject_account_ledger_change() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'account ledger is append-only' USING ERRCODE='23514';
        END $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER account_ledger_append_only
        BEFORE UPDATE OR DELETE ON account_ledger_entries
        FOR EACH ROW EXECUTE FUNCTION reject_account_ledger_change()
        """
    )
    op.execute(
        """
        CREATE FUNCTION validate_account_allocation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
          credit account_ledger_entries%ROWTYPE;
          debit account_ledger_entries%ROWTYPE;
          credit_used numeric;
          debit_used numeric;
        BEGIN
          SELECT * INTO credit FROM account_ledger_entries
            WHERE id=NEW.credit_entry_id FOR UPDATE;
          SELECT * INTO debit FROM account_ledger_entries
            WHERE id=NEW.debit_entry_id FOR UPDATE;
          IF credit.id IS NULL OR debit.id IS NULL
             OR credit.user_id IS DISTINCT FROM NEW.user_id
             OR debit.user_id IS DISTINCT FROM NEW.user_id
             OR credit.amount <= 0
             OR credit.entry_type NOT IN ('payment_credit','admin_adjustment')
             OR debit.entry_type <> 'purchase_debit'
             OR debit.amount >= 0 THEN
            RAISE EXCEPTION 'invalid account allocation identity' USING ERRCODE='23514';
          END IF;
          SELECT coalesce(sum(amount),0) INTO credit_used
            FROM account_ledger_allocations WHERE credit_entry_id=credit.id;
          SELECT coalesce(sum(amount),0) INTO debit_used
            FROM account_ledger_allocations WHERE debit_entry_id=debit.id;
          IF credit_used + NEW.amount > credit.amount
             OR debit_used + NEW.amount > abs(debit.amount) THEN
            RAISE EXCEPTION 'account allocation exceeds source' USING ERRCODE='23514';
          END IF;
          RETURN NEW;
        END $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER account_allocation_validate
        BEFORE INSERT ON account_ledger_allocations
        FOR EACH ROW EXECUTE FUNCTION validate_account_allocation()
        """
    )
    op.execute(
        """
        CREATE FUNCTION reject_account_allocation_change() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'account allocations are append-only' USING ERRCODE='23514';
        END $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER account_allocations_append_only
        BEFORE UPDATE OR DELETE ON account_ledger_allocations
        FOR EACH ROW EXECUTE FUNCTION reject_account_allocation_change()
        """
    )
    op.execute(
        """
        CREATE FUNCTION protect_account_reservation_identity() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF ROW(NEW.user_id,NEW.payment_id,NEW.reservation_type,NEW.amount,
                 NEW.currency,NEW.idempotency_key,NEW.metadata)
             IS DISTINCT FROM
             ROW(OLD.user_id,OLD.payment_id,OLD.reservation_type,OLD.amount,
                 OLD.currency,OLD.idempotency_key,OLD.metadata) THEN
            RAISE EXCEPTION 'immutable reservation identity' USING ERRCODE='23514';
          END IF;
          IF OLD.status <> 'active' AND ROW(NEW.status,NEW.resolved_at)
             IS DISTINCT FROM ROW(OLD.status,OLD.resolved_at) THEN
            RAISE EXCEPTION 'reservation is terminal' USING ERRCODE='23514';
          END IF;
          RETURN NEW;
        END $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER account_reservation_identity
        BEFORE UPDATE ON account_balance_reservations
        FOR EACH ROW EXECUTE FUNCTION protect_account_reservation_identity()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER account_reservation_identity ON account_balance_reservations"
    )
    op.execute("DROP FUNCTION protect_account_reservation_identity()")
    op.execute(
        "DROP TRIGGER account_allocations_append_only ON account_ledger_allocations"
    )
    op.execute("DROP FUNCTION reject_account_allocation_change()")
    op.execute(
        "DROP TRIGGER account_allocation_validate ON account_ledger_allocations"
    )
    op.execute("DROP FUNCTION validate_account_allocation()")
    op.execute("DROP TRIGGER account_ledger_append_only ON account_ledger_entries")
    op.execute("DROP FUNCTION reject_account_ledger_change()")
    op.drop_table("account_balance_reservations")
    op.drop_table("account_ledger_allocations")
    op.drop_table("account_ledger_entries")
    op.drop_index("ix_payments_balance_topup_created", table_name="payments")
    op.drop_index(
        "uq_payments_visible_balance_topup_user", table_name="payments"
    )
    op.drop_constraint(
        "ck_payments_balance_topup_money", "payments", type_="check"
    )
    op.drop_constraint(
        "ck_payments_balance_topup_shape", "payments", type_="check"
    )
    op.drop_constraint("ck_payments_kind", "payments", type_="check")
    op.alter_column("payments", "tariff_id", nullable=False)
    for column in (
        "credit_notified_at",
        "credited_at",
        "topup_context",
        "ui_visible",
        "payment_kind",
    ):
        op.drop_column("payments", column)
    for column in (
        "financial_block_reason",
        "topup_blocked",
        "financial_hold",
    ):
        op.drop_column("users", column)
