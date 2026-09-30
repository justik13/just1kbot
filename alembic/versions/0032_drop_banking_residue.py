"""Drop the abandoned banking schema left behind by the PR #277 service purge.

PR #277 removed ~20 service modules that implemented the exchange/banking billing
model, but it dropped no tables and wrote no migration. These tables have had no
production writer since that purge, yet they still carried indexes, CHECK
constraints, foreign keys and trigger bodies referencing them.

Tables dropped here (all have zero production readers or writers today):

* ``paid_value_ledger``          - multi-layer "paid value in seconds" ledger
* ``payment_provider_operations`` - the banned outbox queue for payment creation
* ``account_balance_reservations`` - clearing-style balance locks/allocations
* ``payment_refunds``           - no ORM model consumer, no code at all
* ``payment_events``            - write-only audit trail, never read
* ``entitlement_entries``       - write-only, all entitlement math is inline on User
* ``payment_disputes``          - no ORM model, no code at all
* ``provider_refund_operations`` - no ORM model, no code at all

``payments`` is deliberately KEPT: the admin payment card, referral logic and the
dashboard still read it.

Revision ID: 0032_drop_banking_residue
Revises: 0031_awg_persistent_traffic
Create Date: 2026-09-30 12:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0032_drop_banking_residue"
down_revision: str | None = "0031_awg_persistent_traffic"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


DROPPED_TABLES = (
    # Child tables first so the FK graph unwinds cleanly.
    "payment_disputes",
    "provider_refund_operations",
    "payment_events",
    "payment_refunds",
    "entitlement_entries",
    "paid_value_ledger",
    "account_balance_reservations",
    "payment_provider_operations",
)


def upgrade() -> None:
    # 1. The tariff-version immutability trigger still probes paid_value_ledger.
    #    tariff_quotes is the only remaining live consumer of tariff_versions, so
    #    the trigger keeps exactly that guarantee and the ledger clause is removed.
    op.execute(
        """
    CREATE OR REPLACE FUNCTION public.reject_tariff_version_history_change() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
    BEGIN
      IF TG_OP='DELETE' OR ROW(
        NEW.tariff_id, NEW.version_number, NEW.name_snapshot, NEW.duration_hours,
        NEW.device_limit, NEW.price_rub, NEW.currency, NEW.created_at,
        NEW.service_type, NEW.base_quota_bytes
      ) IS DISTINCT FROM ROW(
        OLD.tariff_id, OLD.version_number, OLD.name_snapshot, OLD.duration_hours,
        OLD.device_limit, OLD.price_rub, OLD.currency, OLD.created_at,
        OLD.service_type, OLD.base_quota_bytes
      ) THEN
        IF EXISTS(
          SELECT 1 FROM tariff_quotes
          WHERE source_tariff_version_id=OLD.id OR target_tariff_version_id=OLD.id
        ) THEN
          RAISE EXCEPTION 'used tariff version is immutable';
        END IF;
      END IF;
      RETURN COALESCE(NEW,OLD);
    END $$;
    """
    )

    # 2. Drop the reservation identity trigger; it guards a table that is going away.
    op.execute("DROP TRIGGER IF EXISTS account_reservation_identity ON public.account_balance_reservations")
    op.execute("DROP TRIGGER IF EXISTS paid_value_ledger_append_only ON public.paid_value_ledger")
    op.execute("DROP TRIGGER IF EXISTS entitlement_entries_append_only ON public.entitlement_entries")

    # 3. Drop the tables themselves.
    for table in DROPPED_TABLES:
        op.execute(f"DROP TABLE IF EXISTS public.{table} CASCADE")


def downgrade() -> None:
    # No-op by design, and required by the CI migration chain contract
    # (tests.yml: `alembic downgrade base` must succeed and leave no application
    # tables behind).
    #
    # The tables dropped in upgrade() are not recreated. They were the abandoned
    # banking schema, they had no production writer for weeks, and nothing in the
    # codebase reads them any more, so re-creating them would only restore dead
    # structure. The round trip is still consistent: upgrade() drops with
    # IF EXISTS, so re-applying it after a downgrade is idempotent.
    #
    # Recovering the data itself requires restoring a pre-migration backup, which
    # is this project's documented production path.
    pass
