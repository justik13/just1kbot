"""Add desired/actual VPN profile lifecycle and migrate legacy deletions.

Revision ID: d74b3e921c10
Revises: 6f8c2d31a9b4
"""
from alembic import op
import sqlalchemy as sa

revision = "d74b3e921c10"
down_revision = "6f8c2d31a9b4"
branch_labels = None
depends_on = None

STATUSES = "'pending_create','active','pending_update','deleting','create_failed','update_failed','delete_failed'"
ALLOWLIST = "'device_delete_api_failed','create_device_rollback_failed','ban_delete','chargeback_delete','grace_delete','server_delete'"


def upgrade():
    op.add_column("vpn_profiles", sa.Column("client_name", sa.String(255)))
    op.add_column("vpn_profiles", sa.Column("provisioning_status", sa.String(30), nullable=False, server_default="active"))
    op.add_column("vpn_profiles", sa.Column("desired_is_active", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("vpn_profiles", sa.Column("actual_is_active", sa.Boolean()))
    op.add_column("vpn_profiles", sa.Column("desired_expires_at", sa.DateTime(timezone=True)))
    op.add_column("vpn_profiles", sa.Column("actual_expires_at", sa.DateTime(timezone=True)))
    op.add_column("vpn_profiles", sa.Column("desired_version", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("vpn_profiles", sa.Column("last_synced_at", sa.DateTime(timezone=True)))
    op.add_column("vpn_profiles", sa.Column("last_sync_error", sa.Text()))
    op.execute("UPDATE vpn_profiles SET provisioning_status='active', desired_is_active=is_active, actual_is_active=is_active, desired_version=1")
    op.create_check_constraint("ck_vpn_profiles_provisioning_status", "vpn_profiles", f"provisioning_status IN ({STATUSES})")
    op.create_check_constraint("ck_vpn_profiles_desired_version_positive", "vpn_profiles", "desired_version > 0")
    op.drop_index("uq_vpn_profiles_peer_id", table_name="vpn_profiles")
    op.alter_column("vpn_profiles", "peer_id", nullable=True)
    op.alter_column("vpn_profiles", "raw_config", nullable=True)
    op.create_index("uq_vpn_profiles_server_peer_id_not_null", "vpn_profiles", ["server_id", "peer_id"], unique=True, postgresql_where=sa.text("peer_id IS NOT NULL"))
    # Ciphertext is copied by PostgreSQL, never materialized or logged by Python.
    op.execute(f"""
      INSERT INTO api_operations(operation_type,status,idempotency_key,server_name_snapshot,
        api_url_snapshot,api_key_snapshot,peer_id,client_name,payload,attempts,max_attempts,next_attempt_at,created_at,updated_at)
      SELECT 'delete_peer','pending','legacy-delete:'||id,server_name,api_url,api_key,
        peer_id,client_name,jsonb_build_object('managed_workflow',true,'legacy_reason',reason),0,10,now(),created_at,now()
      FROM pending_api_deletions WHERE attempts >= 0 AND reason IN ({ALLOWLIST})
      ON CONFLICT (idempotency_key) DO NOTHING
    """)
    op.execute(f"UPDATE pending_api_deletions SET attempts=-1,last_error='MIGRATED_TO_API_OPERATIONS' WHERE attempts >= 0 AND reason IN ({ALLOWLIST})")


def downgrade():
    op.execute("DELETE FROM api_operations WHERE idempotency_key LIKE 'legacy-delete:%'")
    op.drop_index("uq_vpn_profiles_server_peer_id_not_null", table_name="vpn_profiles")
    pending = op.get_bind().execute(sa.text(
        "SELECT count(*) FROM vpn_profiles WHERE peer_id IS NULL OR raw_config IS NULL"
    )).scalar_one()
    if pending:
        raise RuntimeError(
            "Cannot downgrade VPN lifecycle while pending profiles contain NULL peer/config; "
            "fulfil or remove them explicitly first"
        )
    op.alter_column("vpn_profiles", "raw_config", nullable=False)
    op.alter_column("vpn_profiles", "peer_id", nullable=False)
    op.create_index("uq_vpn_profiles_peer_id", "vpn_profiles", ["peer_id"], unique=True)
    op.drop_constraint("ck_vpn_profiles_desired_version_positive", "vpn_profiles", type_="check")
    op.drop_constraint("ck_vpn_profiles_provisioning_status", "vpn_profiles", type_="check")
    for name in ("last_sync_error","last_synced_at","desired_version","actual_expires_at","desired_expires_at","actual_is_active","desired_is_active","provisioning_status","client_name"):
        op.drop_column("vpn_profiles", name)
