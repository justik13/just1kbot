"""Rename legacy awg service_type to standard across tariffs and orders.

Revision ID: 0038_rename_awg_to_standard
Revises: 0037_vless_subscriptions
Create Date: 2026-10-11 08:30:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0038_rename_awg_to_standard"
down_revision: str | None = "0037_vless_subscriptions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_orders_service_type", "orders", type_="check")

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            """
            DO $$
            DECLARE
                r RECORD;
                std_id INT;
            BEGIN
                FOR r IN
                    SELECT t.id AS awg_id, t.device_limit, t.duration_days
                    FROM tariffs t
                    WHERE TRIM(t.service_type) = 'awg'
                LOOP
                    SELECT s.id INTO std_id
                    FROM tariffs s
                    WHERE s.service_type = 'standard'
                      AND s.device_limit = r.device_limit
                      AND s.duration_days = r.duration_days
                    LIMIT 1;

                    IF std_id IS NOT NULL THEN
                        UPDATE users SET current_tariff_id = std_id WHERE current_tariff_id = r.awg_id;
                        UPDATE orders SET tariff_id = std_id WHERE tariff_id = r.awg_id;
                        DELETE FROM tariffs WHERE id = r.awg_id;
                    END IF;
                END LOOP;
            END $$;
            """
        )

    op.execute("UPDATE tariffs SET service_type = 'standard' WHERE TRIM(service_type) = 'awg'")
    op.execute("UPDATE orders SET service_type = 'standard' WHERE TRIM(service_type) = 'awg'")
    op.alter_column(
        "tariffs",
        "service_type",
        existing_type=sa.String(length=30),
        server_default=sa.text("'standard'"),
    )
    op.alter_column(
        "orders",
        "service_type",
        existing_type=sa.String(length=30),
        server_default=sa.text("'standard'"),
    )
    op.create_check_constraint(
        "ck_orders_service_type",
        "orders",
        "service_type IN ('standard', 'white_internet', 'topup', 'awg')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_orders_service_type", "orders", type_="check")
    op.execute("UPDATE tariffs SET service_type = 'awg' WHERE service_type = 'standard'")
    op.execute("UPDATE orders SET service_type = 'awg' WHERE service_type = 'standard'")
    op.alter_column(
        "tariffs",
        "service_type",
        existing_type=sa.String(length=30),
        server_default=sa.text("'awg'"),
    )
    op.alter_column(
        "orders",
        "service_type",
        existing_type=sa.String(length=30),
        server_default=sa.text("'awg'"),
    )
    op.create_check_constraint(
        "ck_orders_service_type",
        "orders",
        "service_type IN ('awg', 'white_internet', 'topup')",
    )
