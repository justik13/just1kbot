"""Add White Internet durations, tariff version quota, and server lifecycle status.

Revision ID: 0017_white_internet_durations
Revises: 0016_white_internet
Create Date: 2026-09-02 00:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017_white_internet_durations"
down_revision: str | Sequence[str] | None = "0016_white_internet"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. tariff_versions: service_type, backfill, check constraint
    op.add_column(
        "tariff_versions",
        sa.Column("service_type", sa.String(length=30), nullable=False, server_default="awg"),
    )
    op.execute(
        "UPDATE tariff_versions tv SET service_type = t.service_type "
        "FROM tariffs t WHERE tv.tariff_id = t.id"
    )
    op.create_check_constraint(
        "ck_tariff_versions_service_type",
        "tariff_versions",
        "service_type IN ('awg', 'white_internet')",
    )

    # 2. tariff_versions: base_quota_bytes, backfill, check constraint
    op.add_column(
        "tariff_versions",
        sa.Column("base_quota_bytes", sa.BigInteger(), nullable=True),
    )
    op.execute(
        "UPDATE tariff_versions tv SET base_quota_bytes = 53687091200 "
        "FROM tariffs t WHERE tv.tariff_id = t.id AND t.service_type = 'white_internet'"
    )
    op.create_check_constraint(
        "ck_tariff_versions_base_quota_positive",
        "tariff_versions",
        "base_quota_bytes IS NULL OR base_quota_bytes > 0",
    )

    # 3. servers: lifecycle_status, backfill, check constraint, index
    op.add_column(
        "servers",
        sa.Column("lifecycle_status", sa.String(length=30), nullable=False, server_default="ACTIVE"),
    )
    op.execute("UPDATE servers SET lifecycle_status = 'ACTIVE' WHERE lifecycle_status IS NULL")
    op.create_check_constraint(
        "ck_servers_lifecycle_status",
        "servers",
        "lifecycle_status IN ('ACTIVE', 'DECOMMISSIONING', 'DECOMMISSIONED', 'ARCHIVED')",
    )
    op.create_index("ix_servers_lifecycle_status", "servers", ["lifecycle_status"])

    # 4. Update reject_tariff_version_history_change trigger function
    op.execute("""
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
        ) OR EXISTS(
          SELECT 1 FROM paid_value_ledger WHERE tariff_version_id=OLD.id
        ) THEN
          RAISE EXCEPTION 'used tariff version is immutable';
        END IF;
      END IF;
      RETURN COALESCE(NEW,OLD);
    END $$;
    """)


def downgrade() -> None:
    # 1. Revert trigger function
    op.execute("""
    CREATE OR REPLACE FUNCTION public.reject_tariff_version_history_change() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
    BEGIN
      IF TG_OP='DELETE' OR ROW(
        NEW.tariff_id, NEW.version_number, NEW.name_snapshot, NEW.duration_hours,
        NEW.device_limit, NEW.price_rub, NEW.currency, NEW.created_at
      ) IS DISTINCT FROM ROW(
        OLD.tariff_id, OLD.version_number, OLD.name_snapshot, OLD.duration_hours,
        OLD.device_limit, OLD.price_rub, OLD.currency, OLD.created_at
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
    END $$;
    """)

    # 2. servers: drop index, check constraint, lifecycle_status column
    op.drop_index("ix_servers_lifecycle_status", table_name="servers")
    op.drop_constraint("ck_servers_lifecycle_status", "servers", type_="check")
    op.drop_column("servers", "lifecycle_status")

    # 3. tariff_versions: drop base_quota_bytes constraint and column, service_type constraint and column
    op.drop_constraint("ck_tariff_versions_base_quota_positive", "tariff_versions", type_="check")
    op.drop_column("tariff_versions", "base_quota_bytes")
    op.drop_constraint("ck_tariff_versions_service_type", "tariff_versions", type_="check")
    op.drop_column("tariff_versions", "service_type")
