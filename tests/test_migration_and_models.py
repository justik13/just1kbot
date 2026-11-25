"""Unit tests for Alembic migration sequence and SQLAlchemy models."""

from alembic.config import Config
from alembic.script import ScriptDirectory

from config.enums import (
    AccountLedgerEntryType,
    ServerHealthState,
    ServerLifecycleStatus,
    WhiteInternetProvisioningStatus,
    WhiteInternetStatus,
)
from database.models import (
    Server,
    UserDevice,
    VlessSubscription,
    WhiteInternetSubscription,
)


def test_alembic_single_head_and_migration_parent():
    cfg = Config("alembic.ini")
    script = ScriptDirectory.from_config(cfg)
    heads = script.get_heads()
    assert len(heads) == 1, f"Expected exactly 1 Alembic head, found {heads}"
    head_rev = script.get_revision(heads[0])
    assert head_rev.revision == "0049_admin_search_indexes"
    assert head_rev.down_revision == "0048_wi_constraints"


def test_models_orm_columns_and_defaults():
    # Verify VlessSubscription schema
    vless_cols = {c.name for c in VlessSubscription.__table__.columns}
    assert "user_id" in vless_cols
    assert "uuid" in vless_cols
    assert "token" in vless_cols
    assert "is_active" in vless_cols
    assert "device_limit" in vless_cols
    assert "pending_revoked_uuids" in vless_cols
    assert "last_synced_version" in vless_cols
    assert "last_synced_at" in vless_cols

    # Verify WhiteInternetSubscription schema: multi-hub architecture, no hub_server_id pinning
    wi_cols = {c.name for c in WhiteInternetSubscription.__table__.columns}
    assert "user_id" in wi_cols
    assert "uuid" in wi_cols
    assert "node_sync_state" in wi_cols
    assert "node_traffic_snapshots" in wi_cols
    assert "pending_revoked_uuids" in wi_cols
    assert "hub_server_id" not in wi_cols, "hub_server_id must not exist in multi-hub WI model"

    # Verify UserDevice schema
    dev_cols = {c.name for c in UserDevice.__table__.columns}
    assert "user_id" in dev_cols
    assert "hwid" in dev_cols
    assert "device_uuid" in dev_cols
    assert "std_priority" in dev_cols
    assert "bi_priority" in dev_cols
    assert "first_seen_at" in dev_cols
    assert "os_version" in dev_cols
    assert "app_version" in dev_cols

    # Verify Server schema
    server_cols = {c.name for c in Server.__table__.columns}
    assert "role" in server_cols
    assert "capabilities" in server_cols
    assert "health_state" in server_cols
    assert "xray_instance_epoch" in server_cols


def test_enums_consistency():
    assert WhiteInternetStatus.ACTIVE.value == "ACTIVE"
    assert WhiteInternetStatus.EXHAUSTED.value == "EXHAUSTED"
    assert WhiteInternetStatus.DISABLED.value == "DISABLED"
    assert WhiteInternetProvisioningStatus.ACTIVE.value == "ACTIVE"
    assert ServerHealthState.ONLINE.value == "ONLINE"
    assert ServerHealthState.MANUAL_DISABLED.value == "MANUAL_DISABLED"
    assert ServerLifecycleStatus.ACTIVE.value == "ACTIVE"
    assert AccountLedgerEntryType.PAYMENT_CREDIT.value == "payment_credit"
    assert AccountLedgerEntryType.PURCHASE_DEBIT.value == "purchase_debit"
