"""Initial migration

Revision ID: a293fba90064
Revises:
Create Date: 2026-07-28 04:02:56.481199

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from utils.encryption import EncryptedString


# revision identifiers, used by Alembic.
revision: str = 'a293fba90064'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create all tables for just1kbot."""
    # Order matters! Create tables without FK dependencies first.
    # servers and tariffs must exist before users (FK to tariffs.id)
    
    # Servers table (no FK dependencies)
    op.create_table('servers',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('country_flag', sa.String(length=10), nullable=True),
        sa.Column('api_url', sa.String(length=500), nullable=False),
        sa.Column('api_key', EncryptedString(critical=True), nullable=False),
        sa.Column('protocol', sa.String(length=50), nullable=False),
        sa.Column('max_clients', sa.Integer(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('api_url', name='uq_servers_api_url')
    )
    
    # Tariffs table (no FK dependencies)
    op.create_table('tariffs',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('duration_days', sa.Integer(), nullable=False),
        sa.Column('device_limit', sa.Integer(), nullable=False),
        sa.Column('price_rub', sa.Integer(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('sort_order', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('device_limit', 'duration_days', name='uq_tariffs_device_limit_duration_days')
    )
    
    # Users table (FK to tariffs.id - now exists)
    op.create_table('users',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('telegram_id', sa.BigInteger(), nullable=False),
        sa.Column('username', sa.String(length=255), nullable=True),
        sa.Column('first_name', sa.String(length=255), nullable=True),
        sa.Column('subscription_end', sa.DateTime(timezone=True), nullable=True),
        sa.Column('device_limit', sa.Integer(), nullable=False),
        sa.Column('current_tariff_id', sa.Integer(), nullable=True),
        sa.Column('referred_by', sa.BigInteger(), nullable=True),
        sa.Column('referral_days', sa.Integer(), nullable=False),
        sa.Column('last_payment_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('is_banned', sa.Boolean(), nullable=False),
        sa.Column('is_bot_blocked', sa.Boolean(), nullable=False),
        sa.Column('is_deleted', sa.Boolean(), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('notification_retry_count', sa.Integer(), nullable=False),
        sa.Column('last_notification_attempt', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('notified_3d', sa.Boolean(), nullable=False),
        sa.Column('notified_1d', sa.Boolean(), nullable=False),
        sa.Column('notified_2h', sa.Boolean(), nullable=False),
        sa.Column('notified_expired', sa.Boolean(), nullable=False),
        sa.Column('notified_grace_12h', sa.Boolean(), nullable=False),
        sa.Column('device_creations_today', sa.Integer(), nullable=False),
        sa.Column('last_creation_date', sa.Date(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['current_tariff_id'], ['tariffs.id'], ondelete='SET NULL')
    )
    op.create_index(op.f('ix_users_telegram_id'), 'users', ['telegram_id'], unique=True)
    op.create_index(op.f('ix_users_referred_by'), 'users', ['referred_by'], unique=False)
    op.create_index(op.f('ix_users_is_bot_blocked'), 'users', ['is_bot_blocked'], unique=False)
    op.create_index(op.f('ix_users_is_deleted'), 'users', ['is_deleted'], unique=False)
    
    # VPN Profiles table (FK to users.id and servers.id - both exist)
    op.create_table('vpn_profiles',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('server_id', sa.Integer(), nullable=False),
        sa.Column('device_name', sa.String(length=255), nullable=False),
        sa.Column('peer_id', sa.String(length=255), nullable=False),
        sa.Column('raw_config', sa.String(), nullable=False),
        sa.Column('traffic_down', sa.BigInteger(), nullable=False),
        sa.Column('traffic_up', sa.BigInteger(), nullable=False),
        sa.Column('last_connected', sa.DateTime(timezone=True), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['server_id'], ['servers.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE')
    )
    op.create_index(op.f('ix_vpn_profiles_user_id'), 'vpn_profiles', ['user_id'], unique=False)
    op.create_index(op.f('ix_vpn_profiles_server_id'), 'vpn_profiles', ['server_id'], unique=False)
    op.create_index('uq_vpn_profiles_peer_id', 'vpn_profiles', ['peer_id'], unique=True)
    
    # Payments table (FK to users.id and tariffs.id - both exist)
    op.create_table('payments',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('tariff_id', sa.Integer(), nullable=False),
        sa.Column('amount', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('currency', sa.String(length=20), nullable=False),
        sa.Column('status', sa.String(length=30), nullable=False),
        sa.Column('manual_review_reason', sa.String(length=255), nullable=True),
        sa.Column('snapshot_duration_days', sa.Integer(), nullable=True),
        sa.Column('snapshot_device_limit', sa.Integer(), nullable=True),
        sa.Column('snapshot_amount', sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column('snapshot_currency', sa.String(length=20), nullable=True),
        sa.Column('referral_user_bonus_days', sa.Integer(), nullable=False),
        sa.Column('referral_referrer_bonus_days', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('paid_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('external_id', sa.String(length=255), nullable=True),
        sa.Column('payment_url', sa.String(length=1000), nullable=True),
        sa.Column('payment_method', sa.String(length=50), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['tariff_id'], ['tariffs.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE')
    )
    op.create_index(op.f('ix_payments_user_id'), 'payments', ['user_id'], unique=False)
    op.create_index(op.f('ix_payments_status'), 'payments', ['status'], unique=False)
    op.create_index(op.f('ix_payments_status_created_at'), 'payments', ['status', 'created_at'], unique=False)
    op.create_index(op.f('ix_payments_tariff_status'), 'payments', ['tariff_id', 'status'], unique=False)
    op.create_index('uq_payments_external_id_not_null', 'payments', ['external_id'], unique=True,
                    postgresql_where=sa.text("external_id IS NOT NULL"))
    
    # Payment Events table (FK to payments.id - exists)
    op.create_table('payment_events',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('payment_id', sa.Integer(), nullable=False),
        sa.Column('event_type', sa.String(length=100), nullable=False),
        sa.Column('provider_status', sa.String(length=50), nullable=True),
        sa.Column('reason', sa.String(length=255), nullable=True),
        sa.Column('source', sa.String(length=100), nullable=True),
        sa.Column('details', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['payment_id'], ['payments.id'], ondelete='CASCADE')
    )
    op.create_index(op.f('ix_payment_events_payment_id'), 'payment_events', ['payment_id'], unique=False)
    op.create_index('ix_payment_events_payment_created', 'payment_events', ['payment_id', 'created_at'], unique=False)
    
    # Audit Logs table (no FK dependencies)
    op.create_table('audit_logs',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('admin_id', sa.BigInteger(), nullable=False),
        sa.Column('action', sa.String(length=100), nullable=False),
        sa.Column('target_type', sa.String(length=50), nullable=True),
        sa.Column('target_id', sa.BigInteger(), nullable=True),
        sa.Column('details', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_audit_logs_created_at', 'audit_logs', ['created_at'], unique=False)
    
    # Broadcast Progress table (no FK dependencies)
    op.create_table('broadcast_progress',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('admin_id', sa.BigInteger(), nullable=False),
        sa.Column('total_count', sa.Integer(), nullable=False),
        sa.Column('success_count', sa.Integer(), nullable=False),
        sa.Column('fail_count', sa.Integer(), nullable=False),
        sa.Column('last_processed_id', sa.BigInteger(), nullable=False),
        sa.Column('target_audience', sa.String(length=20), nullable=False),
        sa.Column('broadcast_text', sa.Text(), nullable=False),
        sa.Column('media_id', sa.String(length=255), nullable=True),
        sa.Column('content_type', sa.String(length=50), nullable=False),
        sa.Column('label', sa.String(length=50), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_broadcast_progress_admin_id'), 'broadcast_progress', ['admin_id'], unique=False)
    op.create_index(op.f('ix_broadcast_progress_status'), 'broadcast_progress', ['status'], unique=False)
    
    # Pending API Deletions table (no FK dependencies)
    op.create_table('pending_api_deletions',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('server_name', sa.String(length=255), nullable=False),
        sa.Column('api_url', sa.String(length=500), nullable=False),
        sa.Column('api_key', EncryptedString(critical=True), nullable=False),
        sa.Column('peer_id', sa.String(length=255), nullable=False),
        sa.Column('client_name', sa.String(length=255), nullable=True),
        sa.Column('reason', sa.String(length=50), nullable=True),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('last_attempt_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Maintenance Mode table (no FK dependencies)
    op.create_table('maintenance_mode',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('is_enabled', sa.Boolean(), nullable=False),
        sa.Column('message', sa.Text(), nullable=True),
        sa.Column('updated_by', sa.BigInteger(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Hub Messages table (no FK dependencies)
    op.create_table('hub_messages',
        sa.Column('chat_id', sa.BigInteger(), nullable=False),
        sa.Column('message_id', sa.BigInteger(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('chat_id', 'message_id')
    )
    
    # Additional indexes from _apply_additional_indexes (moved to migration)
    # Index for active subscriptions
    op.create_index('ix_users_active_subscription', 'users', ['subscription_end'],
                    postgresql_where=sa.text("is_deleted = false AND subscription_end IS NOT NULL"))
    
    # Index for banned users
    op.create_index('ix_users_banned', 'users', ['telegram_id'],
                    postgresql_where=sa.text("is_banned = true AND is_deleted = false"))
    
    # Index for expiring subscriptions (notifications)
    op.create_index('ix_users_expiring_subscription', 'users', ['subscription_end', 'telegram_id'],
                    postgresql_where=sa.text("""
                        is_deleted = false
                        AND is_bot_blocked = false
                        AND is_banned = false
                        AND subscription_end IS NOT NULL
                        AND (notified_3d = false OR notified_1d = false OR notified_2h = false)
                    """))
    
    # Index for expired grace notifications
    op.create_index('ix_users_expired_grace_notify', 'users', ['subscription_end', 'telegram_id'],
                    postgresql_where=sa.text("""
                        is_deleted = false
                        AND is_bot_blocked = false
                        AND subscription_end IS NOT NULL
                        AND (notified_expired = false OR notified_grace_12h = false)
                    """))
    
    # Index for broadcast in progress
    op.create_index('ix_broadcast_in_progress', 'broadcast_progress', ['status', 'created_at'],
                    postgresql_where=sa.text("status = 'in_progress'"))
    
    # Index for pending API deletions retry
    op.create_index('ix_pending_api_deletions_attempts', 'pending_api_deletions', ['attempts', 'created_at'],
                    postgresql_where=sa.text("attempts < 10"))
    
    # Index for paginated user listing
    op.create_index('ix_users_paginated', 'users', ['created_at', 'id'],
                    postgresql_where=sa.text("is_deleted = false"),
                    postgresql_ops={'created_at': 'DESC', 'id': 'DESC'})
    
    # Index for hub messages chat lookup
    op.create_index('ix_hub_messages_chat_id', 'hub_messages', ['chat_id'])
    
    # Index for audit logs (descending)
    op.create_index('ix_audit_logs_created_at_desc', 'audit_logs', ['created_at'],
                    postgresql_ops={'created_at': 'DESC'})
    
    # Unique index for VPN profiles (user, server, device_name case-insensitive)
    op.create_index('uq_vpn_profiles_user_server_device_name', 'vpn_profiles',
                    ['user_id', 'server_id', sa.func.lower('device_name')], unique=True)


def downgrade() -> None:
    """Drop all tables in reverse order."""
    # Drop indexes first (they depend on tables)
    op.drop_index('uq_vpn_profiles_user_server_device_name', table_name='vpn_profiles')
    op.drop_index('ix_audit_logs_created_at_desc', table_name='audit_logs')
    op.drop_index('ix_hub_messages_chat_id', table_name='hub_messages')
    op.drop_index('ix_users_paginated', table_name='users')
    op.drop_index('ix_pending_api_deletions_attempts', table_name='pending_api_deletions')
    op.drop_index('ix_broadcast_in_progress', table_name='broadcast_progress')
    op.drop_index('ix_users_expired_grace_notify', table_name='users')
    op.drop_index('ix_users_expiring_subscription', table_name='users')
    op.drop_index('ix_users_banned', table_name='users')
    op.drop_index('ix_users_active_subscription', table_name='users')
    
    # Drop tables in reverse dependency order
    op.drop_table('hub_messages')
    op.drop_table('maintenance_mode')
    op.drop_table('pending_api_deletions')
    op.drop_table('broadcast_progress')
    op.drop_table('audit_logs')
    op.drop_table('payment_events')
    op.drop_table('payments')
    op.drop_table('vpn_profiles')
    op.drop_table('users')
    op.drop_table('tariffs')
    op.drop_table('servers')
