"""Initial baseline schema for Just1k 2.0.

Revision ID: 0001_initial_v2
Revises: 
Create Date: 2026-11-08 05:00:00.000000
"""

from alembic import op
from database.models import Base

# revision identifiers, used by Alembic.
revision = "0001_initial_v2"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind)


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind)
