"""payments_referral_bonus_idx

Revision ID: c20a97270920
Revises: bac83372da22
Create Date: 2026-08-21 16:12:12.338575

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = 'c20a97270920'
down_revision: Union[str, Sequence[str], None] = 'bac83372da22'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    if bind and bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute(
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_payments_referral_bonus_unprocessed "
                "ON payments (id) "
                "WHERE NOT (topup_context @> '{\"referral_bonus_processed\": true}'::jsonb);"
            )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_payments_referral_bonus_unprocessed",
        table_name="payments",
        if_exists=True
    )
