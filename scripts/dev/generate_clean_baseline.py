"""Generate the one supported greenfield Alembic baseline from current metadata.

This helper is used only while preparing the pull request.  It creates the
current schema in an empty PostgreSQL 16 database, installs the explicit
append-only/immutability triggers, dumps the resulting schema, and writes a
self-contained Alembic revision.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from database.models import Base
from database import refund_models as _refund_models  # noqa: F401


CUSTOM_DDL = (
    """CREATE FUNCTION public.is_nonnegative_integer_json_array(value jsonb)
    RETURNS boolean LANGUAGE sql IMMUTABLE AS $$
      SELECT jsonb_typeof(value) = 'array' AND NOT EXISTS (
        SELECT 1 FROM jsonb_array_elements(
          CASE WHEN jsonb_typeof(value) = 'array' THEN value ELSE '[]'::jsonb END
        ) item
        WHERE CASE WHEN jsonb_typeof(item) = 'number' THEN
          (item::text)::numeric < 0
          OR trunc((item::text)::numeric) <> (item::text)::numeric
        ELSE true END
      )
    $$""",
    """CREATE FUNCTION public.protect_account_reservation_identity()
    RETURNS trigger LANGUAGE plpgsql AS $$
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
    END $$""",
    """CREATE FUNCTION public.reject_account_allocation_change()
    RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      RAISE EXCEPTION 'account allocations are append-only' USING ERRCODE='23514';
    END $$""",
    """CREATE FUNCTION public.reject_account_ledger_change()
    RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      RAISE EXCEPTION 'account ledger is append-only' USING ERRCODE='23514';
    END $$""",
    """CREATE FUNCTION public.reject_entitlement_history_change()
    RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      RAISE EXCEPTION 'entitlement entries are append-only' USING ERRCODE='23514';
    END $$""",
    """CREATE FUNCTION public.reject_ledger_change()
    RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      RAISE EXCEPTION 'paid-value ledger is append-only' USING ERRCODE='23514';
    END $$""",
    """CREATE FUNCTION public.reject_quote_economic_change()
    RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'quote deletion is forbidden';
      END IF;
      IF ROW(
        NEW.public_id,NEW.user_id,NEW.operation_type,
        NEW.source_tariff_version_id,NEW.target_tariff_version_id,
        NEW.current_paid_hours,NEW.current_paid_value_rub,NEW.bonus_hours,
        NEW.amount_due_rub,NEW.resulting_paid_hours,NEW.resulting_paid_value_rub,
        NEW.resulting_bonus_hours,NEW.rounding_loss_hours,
        NEW.rounding_loss_value_rub,NEW.currency,NEW.expires_at,NEW.created_at,
        NEW.balance_as_of,NEW.source_subscription_end,
        NEW.source_balance_fingerprint,NEW.source_entitlement_entry_ids,
        NEW.source_ledger_entry_ids
      ) IS DISTINCT FROM ROW(
        OLD.public_id,OLD.user_id,OLD.operation_type,
        OLD.source_tariff_version_id,OLD.target_tariff_version_id,
        OLD.current_paid_hours,OLD.current_paid_value_rub,OLD.bonus_hours,
        OLD.amount_due_rub,OLD.resulting_paid_hours,OLD.resulting_paid_value_rub,
        OLD.resulting_bonus_hours,OLD.rounding_loss_hours,
        OLD.rounding_loss_value_rub,OLD.currency,OLD.expires_at,OLD.created_at,
        OLD.balance_as_of,OLD.source_subscription_end,
        OLD.source_balance_fingerprint,OLD.source_entitlement_entry_ids,
        OLD.source_ledger_entry_ids
      ) THEN
        RAISE EXCEPTION 'quote economic fields are immutable';
      END IF;
      IF NEW.status <> OLD.status AND NOT (
        (OLD.status='active' AND NEW.status IN
          ('expired','cancelled','consumed','manual_review')) OR
        (OLD.status='expired' AND NEW.status IN ('consumed','manual_review')) OR
        (OLD.status='cancelled' AND NEW.status='manual_review') OR
        (OLD.status='consumed' AND NEW.status='manual_review')
      ) THEN
        RAISE EXCEPTION 'invalid quote lifecycle transition';
      END IF;
      RETURN NEW;
    END $$""",
    """CREATE FUNCTION public.reject_tariff_version_history_change()
    RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP='DELETE' OR ROW(
        NEW.tariff_id,NEW.version_number,NEW.name_snapshot,NEW.duration_hours,
        NEW.device_limit,NEW.price_rub,NEW.currency,NEW.created_at
      ) IS DISTINCT FROM ROW(
        OLD.tariff_id,OLD.version_number,OLD.name_snapshot,OLD.duration_hours,
        OLD.device_limit,OLD.price_rub,OLD.currency,OLD.created_at
      ) THEN
        IF EXISTS(
          SELECT 1 FROM tariff_quotes
          WHERE source_tariff_version_id=OLD.id OR target_tariff_version_id=OLD.id
        ) OR EXISTS(
          SELECT 1 FROM paid_value_ledger WHERE tariff_version_id=OLD.id
        ) THEN
          RAISE EXCEPTION 'used tariff version is immutable';
        END IF;
      END IF;
      RETURN COALESCE(NEW,OLD);
    END $$""",
    """CREATE FUNCTION public.validate_account_allocation()
    RETURNS trigger LANGUAGE plpgsql AS $$
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
    END $$""",
    """CREATE TRIGGER account_allocation_validate BEFORE INSERT
    ON public.account_ledger_allocations FOR EACH ROW
    EXECUTE FUNCTION public.validate_account_allocation()""",
    """CREATE TRIGGER account_allocations_append_only BEFORE DELETE OR UPDATE
    ON public.account_ledger_allocations FOR EACH ROW
    EXECUTE FUNCTION public.reject_account_allocation_change()""",
    """CREATE TRIGGER account_ledger_append_only BEFORE DELETE OR UPDATE
    ON public.account_ledger_entries FOR EACH ROW
    EXECUTE FUNCTION public.reject_account_ledger_change()""",
    """CREATE TRIGGER account_reservation_identity BEFORE UPDATE
    ON public.account_balance_reservations FOR EACH ROW
    EXECUTE FUNCTION public.protect_account_reservation_identity()""",
    """CREATE TRIGGER entitlement_entries_append_only BEFORE DELETE OR UPDATE
    ON public.entitlement_entries FOR EACH ROW
    EXECUTE FUNCTION public.reject_entitlement_history_change()""",
    """CREATE TRIGGER paid_value_ledger_append_only BEFORE DELETE OR UPDATE
    ON public.paid_value_ledger FOR EACH ROW
    EXECUTE FUNCTION public.reject_ledger_change()""",
    """CREATE TRIGGER tariff_quotes_immutable BEFORE DELETE OR UPDATE
    ON public.tariff_quotes FOR EACH ROW
    EXECUTE FUNCTION public.reject_quote_economic_change()""",
    """CREATE TRIGGER tariff_versions_immutable BEFORE DELETE OR UPDATE
    ON public.tariff_versions FOR EACH ROW
    EXECUTE FUNCTION public.reject_tariff_version_history_change()""",
)


def split_sql(dump: str) -> list[str]:
    statements: list[str] = []
    current: list[str] = []
    single = double = False
    dollar: str | None = None
    index = 0
    while index < len(dump):
        char = dump[index]
        if dollar:
            if dump.startswith(dollar, index):
                current.append(dollar)
                index += len(dollar)
                dollar = None
                continue
            current.append(char)
            index += 1
            continue
        if not single and not double and char == '$':
            end = dump.find('$', index + 1)
            if end != -1:
                tag = dump[index : end + 1]
                if tag == '$$' or tag[1:-1].replace('_', '').isalnum():
                    dollar = tag
                    current.append(tag)
                    index = end + 1
                    continue
        if char == "'" and not double:
            if single and index + 1 < len(dump) and dump[index + 1] == "'":
                current.extend(("'", "'"))
                index += 2
                continue
            single = not single
        elif char == '"' and not single:
            double = not double
        if char == ';' and not single and not double:
            value = ''.join(current).strip()
            if value:
                statements.append(value)
            current = []
        else:
            current.append(char)
        index += 1
    tail = ''.join(current).strip()
    if tail:
        statements.append(tail)
    return statements


def clean_dump(raw: str) -> list[str]:
    kept_lines = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith('--') or stripped.startswith('\\'):
            continue
        if stripped.startswith('SET ') or stripped.startswith('SELECT pg_catalog.set_config'):
            continue
        kept_lines.append(line)
    result = []
    for statement in split_sql('\n'.join(kept_lines)):
        normalized = statement.strip()
        if not normalized:
            continue
        if normalized.startswith('CREATE SCHEMA public'):
            continue
        if ' OWNER TO ' in normalized:
            continue
        result.append(normalized)
    return result


def render_revision(statements: list[str]) -> str:
    tables = [table.name for table in reversed(Base.metadata.sorted_tables)]
    functions = (
        'validate_account_allocation()',
        'reject_tariff_version_history_change()',
        'reject_quote_economic_change()',
        'reject_ledger_change()',
        'reject_entitlement_history_change()',
        'reject_account_ledger_change()',
        'reject_account_allocation_change()',
        'protect_account_reservation_identity()',
        'is_nonnegative_integer_json_array(value jsonb)',
    )
    lines = [
        '"""Create the complete greenfield production schema.\n\n',
        'Revision ID: 0001_clean_baseline\nRevises: None\n"""\n\n',
        'from alembic import op\n\n',
        'revision = "0001_clean_baseline"\n',
        'down_revision = None\nbranch_labels = None\ndepends_on = None\n\n',
        '_SCHEMA_STATEMENTS = (\n',
    ]
    lines.extend(f'    {statement!r},\n' for statement in statements)
    lines.extend([
        ')\n\n',
        'def upgrade() -> None:\n',
        '    bind = op.get_bind()\n',
        '    bind.exec_driver_sql("SET LOCAL check_function_bodies = false")\n',
        '    for statement in _SCHEMA_STATEMENTS:\n',
        '        bind.exec_driver_sql(statement)\n\n',
        'def downgrade() -> None:\n',
        '    bind = op.get_bind()\n',
    ])
    lines.extend(
        f'    bind.exec_driver_sql("DROP TABLE IF EXISTS public.{table} CASCADE")\n'
        for table in tables
    )
    lines.extend(
        f'    bind.exec_driver_sql("DROP FUNCTION IF EXISTS public.{function} CASCADE")\n'
        for function in functions
    )
    return ''.join(lines)


async def main() -> None:
    url = os.environ['DATABASE_URL']
    engine = create_async_engine(url)
    async with engine.begin() as connection:
        await connection.execute(text('DROP SCHEMA public CASCADE'))
        await connection.execute(text('CREATE SCHEMA public'))
        await connection.run_sync(Base.metadata.create_all)
        for statement in CUSTOM_DDL:
            await connection.execute(text(statement))
    await engine.dispose()

    sync_url = url.replace('postgresql+asyncpg://', 'postgresql://', 1)
    raw = subprocess.check_output(
        ['pg_dump', '--schema-only', '--no-owner', '--no-acl', '--dbname', sync_url],
        text=True,
    )
    statements = clean_dump(raw)
    output = Path(os.environ.get('BASELINE_OUTPUT', '/tmp/0001_clean_baseline.py'))
    output.write_text(render_revision(statements), encoding='utf-8')
    compile(output.read_text(encoding='utf-8'), str(output), 'exec')
    print(f'wrote {output} with {len(statements)} statements')


if __name__ == '__main__':
    asyncio.run(main())
