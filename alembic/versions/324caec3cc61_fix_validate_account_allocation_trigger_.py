"""Fix validate_account_allocation trigger admin adjustment

Revision ID: 324caec3cc61
Revises: 0001_clean_baseline
Create Date: 2026-08-07 18:44:59.227397

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '324caec3cc61'
down_revision: Union[str, Sequence[str], None] = '0001_clean_baseline'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("""
    CREATE OR REPLACE FUNCTION public.validate_account_allocation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
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
         OR debit.entry_type NOT IN ('purchase_debit','admin_adjustment')
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
    END $$;
    """)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("""
    CREATE OR REPLACE FUNCTION public.validate_account_allocation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
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
    END $$;
    """)
