"""Add health tracking fields to servers table

Revision ID: 0003_add_server_health_fields
Revises: 0002_system_settings
Create Date: 2026-08-11 21:45:00.000000

"""
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = '0003_add_server_health_fields'
down_revision: Union[str, Sequence[str], None] = '0002_system_settings'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('servers', sa.Column('disabled_reason', sa.String(length=50), nullable=True))
    op.add_column('servers', sa.Column('disabled_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('servers', sa.Column('last_successful_check', sa.DateTime(timezone=True), nullable=True))
    op.add_column('servers', sa.Column('health_state', sa.String(length=30), nullable=False, server_default='ONLINE'))
    op.add_column('servers', sa.Column('problem_started_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('servers', sa.Column('next_check_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('servers', sa.Column('consecutive_fails', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('servers', sa.Column('consecutive_successes', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('servers', sa.Column('recovery_notice_sent', sa.Boolean(), nullable=False, server_default='false'))


def downgrade() -> None:
    op.drop_column('servers', 'recovery_notice_sent')
    op.drop_column('servers', 'consecutive_successes')
    op.drop_column('servers', 'consecutive_fails')
    op.drop_column('servers', 'next_check_at')
    op.drop_column('servers', 'problem_started_at')
    op.drop_column('servers', 'health_state')
    op.drop_column('servers', 'last_successful_check')
    op.drop_column('servers', 'disabled_at')
    op.drop_column('servers', 'disabled_reason')
