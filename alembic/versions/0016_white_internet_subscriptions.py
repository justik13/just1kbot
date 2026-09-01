"""Add White Internet subscriptions and quota grants schema.

Revision ID: 0016_white_internet
Revises: 0015_auto_fulfill_retry_idx
Create Date: 2026-08-30 00:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0016_white_internet"
down_revision: str | Sequence[str] | None = "0015_auto_fulfill_retry_idx"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. tariffs: service_type, backfill, replace unique constraint
    op.add_column(
        "tariffs",
        sa.Column("service_type", sa.String(length=30), nullable=False, server_default="awg"),
    )
    op.execute("UPDATE tariffs SET service_type = 'awg' WHERE service_type IS NULL")
    op.drop_constraint("uq_tariffs_device_limit_duration_days", "tariffs", type_="unique")
    op.create_unique_constraint(
        "uq_tariffs_service_device_duration",
        "tariffs",
        ["service_type", "device_limit", "duration_days"],
    )

    # 2. tariff_quotes: service_type, backfill, check constraint, update checkout partial index
    op.add_column(
        "tariff_quotes",
        sa.Column("service_type", sa.String(length=30), nullable=False, server_default="awg"),
    )
    op.execute("UPDATE tariff_quotes SET service_type = 'awg' WHERE service_type IS NULL")
    op.create_check_constraint(
        "ck_tariff_quotes_service_type",
        "tariff_quotes",
        "service_type IN ('awg', 'white_internet')",
    )
    op.drop_index("uq_tariff_quotes_active_checkout", table_name="tariff_quotes")
    op.create_index(
        "uq_tariff_quotes_active_checkout",
        "tariff_quotes",
        ["user_id", "service_type", "target_tariff_version_id"],
        unique=True,
        postgresql_where=sa.text("status='active' AND operation_type IN ('purchase','renew')"),
    )

    # 3. servers: capabilities (with backfill) and xray_instance_epoch
    op.add_column(
        "servers",
        sa.Column(
            "capabilities",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.execute(
        "UPDATE servers SET capabilities = '[\"amnezia\"]'::jsonb "
        "WHERE capabilities = '[]'::jsonb OR capabilities IS NULL"
    )
    op.add_column(
        "servers",
        sa.Column("xray_instance_epoch", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "servers",
        sa.Column("xray_instance_boot_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "servers",
        sa.Column("xray_instance_starttime", sa.BigInteger(), nullable=True),
    )


    # 4. white_internet_subscriptions table, constraints and indexes
    op.create_table(
        "white_internet_subscriptions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("origin_node_id", sa.Integer(), nullable=False),
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("uuid", sa.String(length=36), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'PENDING'"),
        ),
        sa.Column("status_reason", sa.String(length=50), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "traffic_limit_bytes",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("53687091200"),
        ),
        sa.Column(
            "traffic_used_bytes",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "traffic_uplink_bytes",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "traffic_downlink_bytes",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "traffic_overage_bytes",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "last_uplink_snapshot",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "last_downlink_snapshot",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("traffic_stats_epoch", sa.String(length=64), nullable=True),
        sa.Column(
            "provisioning_status",
            sa.String(length=30),
            nullable=False,
            server_default=sa.text("'PENDING_CREATE'"),
        ),
        sa.Column(
            "desired_version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "actual_version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("last_reconciled_node_epoch", sa.String(length=64), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sync_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'ACTIVE', 'EXHAUSTED', 'EXPIRED', 'DISABLED')",
            name="ck_white_internet_subscriptions_status",
        ),
        sa.CheckConstraint(
            "provisioning_status IN ('PENDING_CREATE', 'ACTIVE', 'PENDING_UPDATE', 'PENDING_DELETE', 'SYNCED_INACTIVE', 'FAILED')",
            name="ck_white_internet_subscriptions_provisioning_status",
        ),
        sa.CheckConstraint(
            "traffic_limit_bytes >= 0 AND traffic_used_bytes >= 0 "
            "AND traffic_uplink_bytes >= 0 AND traffic_downlink_bytes >= 0 "
            "AND last_uplink_snapshot >= 0 AND last_downlink_snapshot >= 0 "
            "AND traffic_overage_bytes >= 0",
            name="ck_white_internet_subscriptions_traffic_nonnegative",
        ),


        sa.ForeignKeyConstraint(["origin_node_id"], ["servers.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("uuid", name="uq_white_internet_subscriptions_uuid"),
    )
    op.create_index(
        "ix_white_internet_subscriptions_user_id",
        "white_internet_subscriptions",
        ["user_id"],
    )
    op.create_index(
        "ix_white_internet_subscriptions_origin_node_id",
        "white_internet_subscriptions",
        ["origin_node_id"],
    )
    op.create_index(
        "ix_white_internet_subscriptions_token",
        "white_internet_subscriptions",
        ["token"],
        unique=True,
    )
    op.create_index(
        "ix_white_internet_subscriptions_status",
        "white_internet_subscriptions",
        ["status"],
    )
    op.create_index(
        "ix_white_internet_subscriptions_expires_at",
        "white_internet_subscriptions",
        ["expires_at"],
    )
    op.create_index(
        "uq_white_internet_live_user",
        "white_internet_subscriptions",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('PENDING', 'ACTIVE', 'EXHAUSTED')"
        ),
    )


    # 5. white_internet_quota_grants table, constraints and indexes
    op.create_table(
        "white_internet_quota_grants",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("subscription_id", sa.Integer(), nullable=False),
        sa.Column("grant_type", sa.String(length=20), nullable=False),
        sa.Column("bytes_granted", sa.BigInteger(), nullable=False),
        sa.Column("bytes_remaining", sa.BigInteger(), nullable=False),
        sa.Column(
            "price_rub",
            sa.Numeric(precision=12, scale=2),
            nullable=False,
            server_default=sa.text("0.00"),
        ),
        sa.Column("quote_id", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "grant_type IN ('BASE', 'TOPUP')",
            name="ck_white_internet_quota_grants_grant_type",
        ),
        sa.CheckConstraint(
            "bytes_granted > 0",
            name="ck_white_internet_quota_grants_bytes_granted_positive",
        ),
        sa.CheckConstraint(
            "bytes_remaining >= 0",
            name="ck_white_internet_quota_grants_bytes_remaining_nonnegative",
        ),
        sa.CheckConstraint(
            "bytes_remaining <= bytes_granted",
            name="ck_white_internet_quota_grants_bytes_remaining_le_granted",
        ),
        sa.CheckConstraint(
            "price_rub >= 0",
            name="ck_white_internet_quota_grants_price_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["quote_id"],
            ["tariff_quotes.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["subscription_id"],
            ["white_internet_subscriptions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "subscription_id",
            "quote_id",
            "grant_type",
            name="uq_white_internet_quota_grants_sub_quote_type",
        ),
    )
    op.create_index(
        "ix_white_internet_quota_grants_subscription_id",
        "white_internet_quota_grants",
        ["subscription_id"],
    )
    op.create_index(
        "ix_white_internet_quota_grants_quote_id",
        "white_internet_quota_grants",
        ["quote_id"],
    )
    op.create_index(
        "ix_white_internet_quota_grants_expires_at",
        "white_internet_quota_grants",
        ["expires_at"],
    )

    # 6. white_internet_traffic_events table, constraints and indexes
    op.create_table(
        "white_internet_traffic_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("subscription_id", sa.Integer(), nullable=False),
        sa.Column("node_epoch", sa.String(length=64), nullable=False),
        sa.Column("node_boot_id", sa.String(length=64), nullable=True),
        sa.Column("node_starttime", sa.BigInteger(), nullable=True),
        sa.Column("snapshot_uplink_before", sa.BigInteger(), nullable=False),
        sa.Column("snapshot_uplink_after", sa.BigInteger(), nullable=False),
        sa.Column("snapshot_downlink_before", sa.BigInteger(), nullable=False),
        sa.Column("snapshot_downlink_after", sa.BigInteger(), nullable=False),
        sa.Column("delta_uplink", sa.BigInteger(), nullable=False),
        sa.Column("delta_downlink", sa.BigInteger(), nullable=False),
        sa.Column("allocated_bytes", sa.BigInteger(), nullable=False),
        sa.Column("overage_bytes", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "delta_uplink >= 0 AND delta_downlink >= 0",
            name="ck_white_internet_traffic_events_deltas_nonnegative",
        ),
        sa.CheckConstraint(
            "allocated_bytes >= 0 AND overage_bytes >= 0",
            name="ck_white_internet_traffic_events_alloc_nonnegative",
        ),
        sa.CheckConstraint(
            "snapshot_uplink_after >= snapshot_uplink_before OR snapshot_uplink_before = 0",
            name="ck_white_internet_traffic_events_uplink_monotonic",
        ),
        sa.CheckConstraint(
            "snapshot_downlink_after >= snapshot_downlink_before OR snapshot_downlink_before = 0",
            name="ck_white_internet_traffic_events_downlink_monotonic",
        ),
        sa.CheckConstraint(
            "allocated_bytes + overage_bytes = delta_uplink + delta_downlink",
            name="ck_white_internet_traffic_events_conservation",
        ),
        sa.CheckConstraint(
            "delta_uplink = snapshot_uplink_after - snapshot_uplink_before",
            name="ck_white_internet_traffic_events_delta_uplink_arithmetic",
        ),
        sa.CheckConstraint(
            "delta_downlink = snapshot_downlink_after - snapshot_downlink_before",
            name="ck_white_internet_traffic_events_delta_downlink_arithmetic",
        ),
        sa.CheckConstraint(
            "allocated_bytes <= delta_uplink + delta_downlink",
            name="ck_white_internet_traffic_events_allocated_le_delta",
        ),
        sa.CheckConstraint(
            "overage_bytes <= delta_uplink + delta_downlink",
            name="ck_white_internet_traffic_events_overage_le_delta",
        ),
        sa.ForeignKeyConstraint(
            ["subscription_id"],
            ["white_internet_subscriptions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "subscription_id",
            "node_epoch",
            "snapshot_uplink_after",
            "snapshot_downlink_after",
            name="uq_white_internet_traffic_event_snapshot",
        ),
    )
    op.create_index(
        "ix_white_internet_traffic_events_subscription_id",
        "white_internet_traffic_events",
        ["subscription_id"],
    )
    op.create_index(
        "ix_white_internet_traffic_events_created_at",
        "white_internet_traffic_events",
        ["created_at"],
    )

    # 7. Seed canonical default White Internet Tariff and TariffVersion
    op.execute(
        sa.text(
            """
            INSERT INTO tariffs (name, service_type, device_limit, duration_days, price_rub, is_active, sort_order, created_at)
            VALUES ('Белый Интернет 50 ГБ', 'white_internet', 1, 30, 250, true, 0, now())
            ON CONFLICT (service_type, device_limit, duration_days) DO NOTHING
            """
        )
    )


    op.execute(
        sa.text(
            """
            INSERT INTO tariff_versions (tariff_id, version_number, name_snapshot, duration_hours, device_limit, price_rub, currency, created_at)
            SELECT id, 1, 'Белый Интернет 50 ГБ', 720, 1, 250.00, 'RUB', now()
            FROM tariffs
            WHERE service_type = 'white_internet' AND device_limit = 1 AND duration_days = 30
            ON CONFLICT (tariff_id, version_number) DO NOTHING
            """
        )
    )




def downgrade() -> None:
    # 1. Drop white_internet_traffic_events
    op.drop_index(
        "ix_white_internet_traffic_events_created_at",
        table_name="white_internet_traffic_events",
    )
    op.drop_index(
        "ix_white_internet_traffic_events_subscription_id",
        table_name="white_internet_traffic_events",
    )
    op.drop_table("white_internet_traffic_events")

    # 2. Drop white_internet_quota_grants
    op.drop_index(
        "ix_white_internet_quota_grants_expires_at",
        table_name="white_internet_quota_grants",
    )
    op.drop_index(
        "ix_white_internet_quota_grants_quote_id",
        table_name="white_internet_quota_grants",
    )
    op.drop_index(
        "ix_white_internet_quota_grants_subscription_id",
        table_name="white_internet_quota_grants",
    )
    op.drop_table("white_internet_quota_grants")


    # 4. Drop white_internet_subscriptions
    op.drop_index(
        "uq_white_internet_live_user",
        table_name="white_internet_subscriptions",
    )
    op.drop_index(
        "ix_white_internet_subscriptions_expires_at",
        table_name="white_internet_subscriptions",
    )
    op.drop_index(
        "ix_white_internet_subscriptions_status",
        table_name="white_internet_subscriptions",
    )
    op.drop_index(
        "ix_white_internet_subscriptions_token",
        table_name="white_internet_subscriptions",
    )
    op.drop_index(
        "ix_white_internet_subscriptions_origin_node_id",
        table_name="white_internet_subscriptions",
    )
    op.drop_index(
        "ix_white_internet_subscriptions_user_id",
        table_name="white_internet_subscriptions",
    )
    op.drop_table("white_internet_subscriptions")

    # 4. Clean up White Internet tariff quotes, ledger entries, tariff versions and tariffs
    op.execute(sa.text("ALTER TABLE account_ledger_allocations DISABLE TRIGGER USER"))
    op.execute(sa.text("ALTER TABLE account_ledger_entries DISABLE TRIGGER USER"))
    op.execute(sa.text("ALTER TABLE tariff_quotes DISABLE TRIGGER USER"))

    op.execute(
        sa.text(
            """
            DELETE FROM account_ledger_allocations WHERE debit_entry_id IN (
                SELECT id FROM account_ledger_entries WHERE quote_id IN (
                    SELECT id FROM tariff_quotes WHERE service_type = 'white_internet'
                )
            ) OR credit_entry_id IN (
                SELECT id FROM account_ledger_entries WHERE quote_id IN (
                    SELECT id FROM tariff_quotes WHERE service_type = 'white_internet'
                )
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            DELETE FROM account_ledger_entries WHERE quote_id IN (
                SELECT id FROM tariff_quotes WHERE service_type = 'white_internet'
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            DELETE FROM tariff_quotes WHERE service_type = 'white_internet'
            """
        )
    )
    op.execute(
        sa.text(
            """
            DELETE FROM tariff_versions WHERE tariff_id IN (
                SELECT id FROM tariffs WHERE service_type = 'white_internet'
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            DELETE FROM tariffs WHERE service_type = 'white_internet'
            """
        )
    )

    op.execute(sa.text("ALTER TABLE tariff_quotes ENABLE TRIGGER USER"))
    op.execute(sa.text("ALTER TABLE account_ledger_entries ENABLE TRIGGER USER"))
    op.execute(sa.text("ALTER TABLE account_ledger_allocations ENABLE TRIGGER USER"))

    # 5. servers: drop starttime, boot_id, xray_instance_epoch and capabilities


    op.drop_column("servers", "xray_instance_starttime")
    op.drop_column("servers", "xray_instance_boot_id")
    op.drop_column("servers", "xray_instance_epoch")
    op.drop_column("servers", "capabilities")

    # 2. tariff_quotes: revert index, check constraint, drop service_type
    op.drop_index("uq_tariff_quotes_active_checkout", table_name="tariff_quotes")
    op.create_index(
        "uq_tariff_quotes_active_checkout",
        "tariff_quotes",
        ["user_id", "target_tariff_version_id"],
        unique=True,
        postgresql_where=sa.text("status='active' AND operation_type IN ('purchase','renew')"),
    )
    op.drop_constraint(
        "ck_tariff_quotes_service_type",
        "tariff_quotes",
        type_="check",
    )
    op.drop_column("tariff_quotes", "service_type")

    # 1. tariffs: revert constraint, drop service_type
    op.drop_constraint("uq_tariffs_service_device_duration", "tariffs", type_="unique")
    op.create_unique_constraint(
        "uq_tariffs_device_limit_duration_days",
        "tariffs",
        ["device_limit", "duration_days"],
    )
    op.drop_column("tariffs", "service_type")
