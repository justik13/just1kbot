"""Drop the retired tariff quote/version banking schema.

White Internet checkout now runs on wallet Orders (see 0033): the quote
lifecycle (15-minute TTL rows, active-checkout locks, hours/value math,
version snapshots) has no production writer left.

Consumed quotes are backfilled as paid wallet Orders first, and their
ledger debits are re-linked, so admin purchase history and trial checks
(which read Orders) keep working across the boundary. Non-consumed
quotes never moved money and are dropped with the tables.

Tables dropped here (all have zero production readers or writers today):
* ``tariff_quotes``            - exchange-style checkout quotes
* ``tariff_versions``          - price snapshots for quotes

Revision ID: 0034_drop_tariff_quotes
Revises: 0033_wi_order_checkout
Create Date: 2026-10-04 12:00:00.000000
"""

from collections.abc import Sequence
import json
import uuid

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0034_drop_tariff_quotes"
down_revision: str | None = "0033_wi_order_checkout"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _backfill_consumed_quotes(bind) -> None:
    """Create paid wallet Orders for consumed quotes and re-link debits."""
    quotes = bind.execute(
        sa.text(
            "SELECT q.id, q.user_id, q.service_type, q.operation_type, "
            "q.amount_due_rub, q.created_at, q.consumed_at, "
            "tv.tariff_id, tv.duration_hours, tv.device_limit, "
            "q.resulting_paid_hours, tv.name_snapshot "
            "FROM tariff_quotes q "
            "JOIN tariff_versions tv ON tv.id = q.target_tariff_version_id "
            "WHERE q.status = 'consumed' ORDER BY q.id"
        )
    ).fetchall()
    for row in quotes:
        (
            qid,
            user_id,
            service_type,
            operation_type,
            amount_due,
            created_at,
            consumed_at,
            tariff_id,
            duration_hours,
            device_limit,
            resulting_paid_hours,
            name_snapshot,
        ) = row
        order_id = uuid.uuid4()
        paid_at = consumed_at or created_at
        traffic_bytes = 0
        effective_op = operation_type
        if operation_type == "trial":
            days = int(resulting_paid_hours or 72) // 24
        elif operation_type in ("purchase", "renew", "change"):
            if resulting_paid_hours is not None and resulting_paid_hours == 0:
                days = 0
                if service_type == "white_internet":
                    amt = int(amount_due or 0)
                    if amt in (40, 100):
                        effective_op = "topup"
                        traffic_bytes = {40: 10, 100: 25}[amt] * 1024**3
                    elif amt == 200:
                        # 200 RUB in White Internet was either 50 GiB top-up or an additional
                        # device slot (both granted 50 GiB of extra traffic).
                        effective_op = "topup_or_device_slot"
                        traffic_bytes = 50 * 1024**3
            else:
                effective_hours = resulting_paid_hours or duration_hours or 0
                days = int(effective_hours) // 24
        else:
            days = 0
        bind.execute(
            sa.text(
                "INSERT INTO orders (id, user_id, service_type, tariff_id, "
                "amount_rub, duration_days, traffic_bytes, device_limit, payment_method, "
                "status, paid_at, created_at, metadata) "
                "VALUES (:id, :user_id, :service_type, :tariff_id, :amount, "
                ":days, :traffic_bytes, :devices, 'wallet', 'paid', :paid_at, :created_at, "
                "CAST(:metadata AS jsonb))"
            ),
            {
                "id": order_id,
                "user_id": user_id,
                "service_type": service_type,
                "tariff_id": tariff_id,
                "amount": amount_due,
                "days": days,
                "traffic_bytes": traffic_bytes,
                "devices": device_limit,
                "paid_at": paid_at,
                "created_at": created_at,
                "metadata": json.dumps(
                    {
                        "operation": effective_op,
                        "is_trial": operation_type == "trial",
                        "migrated_from_quote": qid,
                        "tariff_name": name_snapshot,
                    }
                ),
            },
        )
        # Re-link every ledger row (debits and their reversals) to the order.
        bind.execute(
            sa.text(
                "UPDATE account_ledger_entries SET order_id = :oid, "
                "quote_id = NULL WHERE quote_id = :qid"
            ),
            {"oid": order_id, "qid": qid},
        )
    leftover = bind.execute(
        sa.text(
            "SELECT count(*) FROM account_ledger_entries "
            "WHERE quote_id IS NOT NULL"
        )
    ).scalar()
    if leftover:
        raise RuntimeError(
            f"drop tariff_quotes aborted: {leftover} ledger rows still reference quotes"
        )


def upgrade() -> None:
    bind = op.get_bind()
    _backfill_consumed_quotes(bind)
    op.execute("DROP TABLE IF EXISTS public.tariff_quotes CASCADE")
    op.execute("DROP TABLE IF EXISTS public.tariff_versions CASCADE")
    op.execute("DROP FUNCTION IF EXISTS public.reject_quote_economic_change()")
    op.execute(
        "DROP FUNCTION IF EXISTS public.reject_tariff_version_history_change()"
    )


def downgrade() -> None:
    # No-op by design, following migration 0032: the dropped tables carried
    # exchange-style checkout state with no production readers left, and the
    # consumed history now lives on as wallet Orders. Re-creating them would
    # only restore dead structure without restoring the checkout flows.
    # Recovering the pre-migration shape requires restoring a backup, which
    # is this project's documented production path.
    pass
