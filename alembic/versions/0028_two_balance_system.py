"""Add balance and bonus_balance to users, relax ledger shape constraint, and backfill balances.

Revision ID: 0028_two_balance_system
Revises: 0027_awg_subscription_system
Create Date: 2026-09-13 14:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0028_two_balance_system"
down_revision: str | None = "0027_awg_subscription_system"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_SHAPE_CONSTRAINT = """
(entry_type = 'payment_credit' AND amount > 0 AND payment_id IS NOT NULL AND quote_id IS NULL AND reversal_of_id IS NULL) OR
(entry_type = 'purchase_debit' AND amount < 0 AND payment_id IS NULL AND quote_id IS NOT NULL AND reversal_of_id IS NULL) OR
(entry_type = 'purchase_reversal' AND amount > 0 AND payment_id IS NULL AND quote_id IS NOT NULL AND reversal_of_id IS NOT NULL) OR
(entry_type IN ('refund_debit','chargeback_debit') AND amount < 0 AND payment_id IS NOT NULL AND quote_id IS NULL AND reversal_of_id IS NULL) OR
(entry_type = 'admin_adjustment' AND payment_id IS NULL AND quote_id IS NULL AND reversal_of_id IS NULL)
"""

NEW_SHAPE_CONSTRAINT = """
(entry_type = 'payment_credit' AND amount > 0 AND payment_id IS NOT NULL AND quote_id IS NULL AND reversal_of_id IS NULL) OR
(entry_type = 'purchase_debit' AND amount < 0 AND payment_id IS NULL AND reversal_of_id IS NULL) OR
(entry_type = 'purchase_reversal' AND amount > 0 AND payment_id IS NULL AND reversal_of_id IS NOT NULL) OR
(entry_type IN ('refund_debit','chargeback_debit') AND amount < 0 AND payment_id IS NOT NULL AND quote_id IS NULL AND reversal_of_id IS NULL) OR
(entry_type = 'admin_adjustment' AND payment_id IS NULL AND quote_id IS NULL AND reversal_of_id IS NULL)
"""

BACKFILL_BALANCES_SQL = """
WITH user_balances AS (
    SELECT
        u.id AS user_id,
        COALESCE(SUM(CASE WHEN e.entry_type = 'payment_credit' THEN e.amount ELSE 0 END), 0) -
        COALESCE(SUM(CASE WHEN e.entry_type IN ('refund_debit', 'chargeback_debit') THEN ABS(e.amount) ELSE 0 END), 0) -
        COALESCE((
            SELECT SUM(a.amount)
            FROM account_ledger_allocations a
            JOIN account_ledger_entries cred ON a.credit_entry_id = cred.id
            WHERE a.user_id = u.id
              AND cred.entry_type = 'payment_credit'
              AND a.debit_entry_id NOT IN (
                  SELECT rev.reversal_of_id
                  FROM account_ledger_entries rev
                  WHERE rev.entry_type = 'purchase_reversal' AND rev.reversal_of_id IS NOT NULL
              )
        ), 0) AS real_avail,
        COALESCE(SUM(CASE WHEN e.entry_type = 'admin_adjustment' AND e.amount > 0 THEN e.amount ELSE 0 END), 0) -
        COALESCE((
            SELECT SUM(a.amount)
            FROM account_ledger_allocations a
            JOIN account_ledger_entries cred ON a.credit_entry_id = cred.id
            WHERE a.user_id = u.id
              AND cred.entry_type = 'admin_adjustment'
              AND a.debit_entry_id NOT IN (
                  SELECT rev.reversal_of_id
                  FROM account_ledger_entries rev
                  WHERE rev.entry_type = 'purchase_reversal' AND rev.reversal_of_id IS NOT NULL
              )
        ), 0) AS bonus_avail
    FROM users u
    LEFT JOIN account_ledger_entries e ON u.id = e.user_id
    GROUP BY u.id
)
UPDATE users u
SET
    balance = ub.real_avail,
    bonus_balance = GREATEST(0, ub.bonus_avail)
FROM user_balances ub
WHERE u.id = ub.user_id
"""

UPDATE_PERMANENT_SUBS_SQL = """
UPDATE users
SET subscription_end = NOW() + INTERVAL '365 days'
WHERE subscription_end >= '2099-01-01'::timestamptz
"""


def upgrade() -> None:
    # 1. Add balance and bonus_balance columns to users table
    op.add_column(
        "users",
        sa.Column(
            "balance",
            sa.Numeric(12, 2),
            nullable=False,
            server_default=sa.text("'0.00'"),
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "bonus_balance",
            sa.Numeric(12, 2),
            nullable=False,
            server_default=sa.text("'0.00'"),
        ),
    )
    op.create_check_constraint(
        "ck_users_bonus_balance_nonnegative",
        "users",
        "bonus_balance >= 0",
    )

    # 2. Relax shape constraint on account_ledger_entries to allow purchase_debit without quote_id
    op.drop_constraint("ck_account_ledger_entry_shape", "account_ledger_entries", type_="check")
    op.create_check_constraint(
        "ck_account_ledger_entry_shape",
        "account_ledger_entries",
        NEW_SHAPE_CONSTRAINT,
    )

    # 3. Backfill users.balance and users.bonus_balance from ledger history
    op.execute(BACKFILL_BALANCES_SQL)
    op.execute(UPDATE_PERMANENT_SUBS_SQL)


def downgrade() -> None:
    # 1. Restore previous strict shape constraint
    op.drop_constraint("ck_account_ledger_entry_shape", "account_ledger_entries", type_="check")
    op.create_check_constraint(
        "ck_account_ledger_entry_shape",
        "account_ledger_entries",
        OLD_SHAPE_CONSTRAINT,
    )

    # 2. Remove constraints and columns from users
    op.drop_constraint("ck_users_bonus_balance_nonnegative", "users", type_="check")
    op.drop_column("users", "bonus_balance")
    op.drop_column("users", "balance")
