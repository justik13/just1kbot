"""hub_messages: durable effect-message marker

Adds `is_effect_message` to `hub_messages` so render_hub can restore the
"clean hub after an effect success screen" invariant after a process restart.

Revision ID: 0011_hub_effect_flag
Revises: 324caec3cc61
Create Date: 2026-08-25
"""
from alembic import op
import sqlalchemy as sa

revision: str = "0011_hub_effect_flag"
down_revision: str | None = "c20a97270920"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "hub_messages",
        sa.Column(
            "is_effect_message",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("hub_messages", "is_effect_message")
