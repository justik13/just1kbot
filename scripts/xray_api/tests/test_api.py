import json
import os
import tempfile
from unittest.mock import patch
from fastapi.testclient import TestClient

# Ensure environment is configured before importing app
tmp_clients_file = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
tmp_clients_file.close()
tmp_epoch_file = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
tmp_epoch_file.close()
tmp_epoch_lock = tempfile.NamedTemporaryFile(delete=False, suffix=".lock")
tmp_epoch_lock.close()

os.environ["XRAY_API_KEY"] = "test-secret-key-12345"
os.environ["CLIENTS_FILE_PATH"] = tmp_clients_file.name
os.environ["EPOCH_FILE_PATH"] = tmp_epoch_file.name
os.environ["EPOCH_LOCK_PATH"] = tmp_epoch_lock.name
os.environ["XRAY_INBOUND_TAGS"] = "inbound-de,inbound-nl"

from app import (  # noqa: E402
    app,
    client_store,
    epoch_manager,
    get_secret_base_path,
    get_target_inbounds,
    grpc_client,
    node_sync_state,
    restore_persisted_clients_to_xray,
)

client = TestClient(app)
VALID_HEADERS = {"X-API-Key": "test-secret-key-12345"}
INVALID_HEADERS = {"X-API-Key": "wrong-key"}


def test_auth_enforcement():
    # Missing header
    res = client.get("/v1/health")
    assert res.status_code == 401

    # Wrong header
    res = client.get("/v1/health", headers=INVALID_HEADERS)
    assert res.status_code == 401

    # Correct header
    with patch.object(grpc_client, "is_healthy", return_value=True):
        with patch.object(
            epoch_manager,
            "get_process_and_epoch",
            return_value=(100, 1000, "boot-1", "epoch_1"),
        ):
            res = client.get("/v1/health", headers=VALID_HEADERS)
            assert res.status_code == 200
            data = res.json()
            assert data["status"] == "ok"
            assert data["xray_running"] is True
            assert data["grpc_ok"] is True


def test_traffic_snapshot():
    mock_stats = {
        "a2b9d4e1-73c5-4812-b964-f3e7b85a1902": {"uplink": 1024, "downlink": 2048}
    }
    with patch.object(grpc_client, "is_healthy", return_value=True):
        with patch.object(grpc_client, "get_users_stats", return_value=mock_stats):
            with patch.object(
                epoch_manager,
                "get_process_and_epoch",
                return_value=(100, 1000, "boot-1", "epoch_1"),
            ):
                res = client.get("/v1/traffic/snapshot", headers=VALID_HEADERS)
                assert res.status_code == 200
                data = res.json()
                assert data["users"] == mock_stats
                assert "timestamp" in data
                assert data["node_epoch"] == "epoch_1"


def test_traffic_snapshot_epoch_drift_retry():
    """F01: Double-checked epoch loop retries on drift and fails closed with 503 if drift persists."""
    mock_stats = {
        "a2b9d4e1-73c5-4812-b964-f3e7b85a1902": {"uplink": 500, "downlink": 1500}
    }

    # Case 1: Drift resolved on retry attempt 2
    # Call 1 (attempt 1 before): pid 100, epoch-1
    # Call 2 (attempt 1 after): pid 101, epoch-2 -> drift!
    # Call 3 (attempt 2 before): pid 101, epoch-2
    # Call 4 (attempt 2 after): pid 101, epoch-2 -> match!
    drift_resolved_sequence = [
        (100, 1000, "boot-1", "epoch_1"),
        (101, 1050, "boot-1", "epoch_2"),
        (101, 1050, "boot-1", "epoch_2"),
        (101, 1050, "boot-1", "epoch_2"),
    ]

    with patch.object(grpc_client, "is_healthy", return_value=True):
        with patch.object(grpc_client, "get_users_stats", return_value=mock_stats):
            with patch.object(
                epoch_manager,
                "get_process_and_epoch",
                side_effect=drift_resolved_sequence,
            ):
                res = client.get("/v1/traffic/snapshot", headers=VALID_HEADERS)
                assert res.status_code == 200
                data = res.json()
                assert data["node_epoch"] == "epoch_2"
                assert data["users"] == mock_stats

    # Case 2: Unresolvable continuous drift -> fail-closed HTTP 503 EpochMismatchError
    continuous_drift = [
        (100, 1000, "boot-1", f"epoch_drift_{i}") for i in range(10)
    ]
    with patch.object(grpc_client, "is_healthy", return_value=True):
        with patch.object(grpc_client, "get_users_stats", return_value=mock_stats):
            with patch.object(
                epoch_manager,
                "get_process_and_epoch",
                side_effect=continuous_drift,
            ):
                res = client.get("/v1/traffic/snapshot", headers=VALID_HEADERS)
                assert res.status_code == 503
                assert "EpochMismatchError" in res.json().get("detail", "")


def test_startup_reconciliation_db_authority():
    """F08: Local clients.json cache is ephemeral hint on boot; node starts unsynchronized until Central DB reconciles."""
    # Setup initial local client cache
    uuid_active = "a2b9d4e1-73c5-4812-b964-f3e7b85a1901"
    uuid_stale = "a2b9d4e1-73c5-4812-b964-f3e7b85a1902"
    client_store.save_clients({uuid_active, uuid_stale})

    # Simulate daemon startup
    node_sync_state["status"] = "unsynchronized"
    node_sync_state["last_synced_at"] = None

    with patch.object(grpc_client, "is_healthy", return_value=True):
        with patch.object(grpc_client, "add_user", return_value=True):
            with patch.object(
                epoch_manager,
                "get_process_and_epoch",
                return_value=(100, 1000, "boot-1", "epoch_1"),
            ):
                restored_count = restore_persisted_clients_to_xray()
                assert restored_count == 2

                # Health reports unsynchronized on startup
                res = client.get("/v1/health", headers=VALID_HEADERS)
                assert res.status_code == 200
                data = res.json()
                assert data["sync_status"] == "unsynchronized"
                assert data["synchronized"] is False

    # Central DB reconciliation executes desired-state alignment:
    # uuid_active is confirmed active with version 2
    # uuid_stale was revoked in Central DB, so it is disabled with version 2
    with patch.object(grpc_client, "add_user", return_value=True):
        res1 = client.post(
            "/v1/clients/sync",
            json={"client_id": uuid_active, "desired_state": "active", "version": 2},
            headers=VALID_HEADERS,
        )
        assert res1.status_code == 200
        assert res1.json()["result"] == "applied"

    with patch.object(grpc_client, "remove_user", return_value=True):
        res2 = client.post(
            "/v1/clients/sync",
            json={"client_id": uuid_stale, "desired_state": "disabled", "version": 2},
            headers=VALID_HEADERS,
        )
        assert res2.status_code == 200
        assert res2.json()["result"] == "applied"

    # Node is now synchronized according to Central DB authority
    with patch.object(grpc_client, "is_healthy", return_value=True):
        with patch.object(
            epoch_manager,
            "get_process_and_epoch",
            return_value=(100, 1000, "boot-1", "epoch_1"),
        ):
            health_after = client.get("/v1/health", headers=VALID_HEADERS).json()
            assert health_after["sync_status"] == "synchronized"
            assert health_after["synchronized"] is True
            assert uuid_active in client_store.load_clients()
            assert uuid_stale not in client_store.load_clients()


def test_client_sync_active_and_persistence():
    uuid = "a2b9d4e1-73c5-4812-b964-f3e7b85a1902"
    with patch.object(grpc_client, "add_user", return_value=True) as mock_add:
        res = client.post(
            "/v1/clients/sync",
            json={"client_id": uuid, "desired_state": "active"},
            headers=VALID_HEADERS,
        )
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ok"
        assert data["result"] == "applied"
        assert data["client_id"] == uuid
        assert data["state"] == "active"
        assert mock_add.call_count == 2
        # Check persisted client store
        assert uuid in client_store.load_clients()


def test_client_sync_disabled():
    uuid = "a2b9d4e1-73c5-4812-b964-f3e7b85a1902"
    with patch.object(grpc_client, "remove_user", return_value=True) as mock_remove:
        res = client.post(
            "/v1/clients/sync",
            json={"uuid": uuid, "desired_state": "disabled"},
            headers=VALID_HEADERS,
        )
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ok"
        assert data["result"] == "applied"
        assert data["client_id"] == uuid
        assert data["state"] == "disabled"
        assert mock_remove.call_count == 2
        # Check removed from client store
        assert uuid not in client_store.load_clients()


def test_client_sync_invalid_uuid():
    res = client.post(
        "/v1/clients/sync",
        json={"client_id": "not-a-valid-uuid", "desired_state": "active"},
        headers=VALID_HEADERS,
    )
    assert res.status_code == 422


def test_client_sync_invalid_state():
    uuid = "a2b9d4e1-73c5-4812-b964-f3e7b85a1902"
    res = client.post(
        "/v1/clients/sync",
        json={"client_id": uuid, "desired_state": "unknown_status"},
        headers=VALID_HEADERS,
    )
    assert res.status_code == 422


def test_client_delete():
    uuid = "a2b9d4e1-73c5-4812-b964-f3e7b85a1902"
    client_store.add_client(uuid)
    with patch.object(grpc_client, "remove_user", return_value=True) as mock_remove:
        res = client.delete(f"/v1/clients/{uuid}", headers=VALID_HEADERS)
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ok"
        assert data["result"] == "applied"
        assert data["client_id"] == uuid
        assert data["action"] == "deleted"
        assert mock_remove.call_count == 2
        assert uuid not in client_store.load_clients()


def test_client_delete_invalid_uuid():
    res = client.delete("/v1/clients/invalid-uuid", headers=VALID_HEADERS)
    assert res.status_code == 422


def test_client_sync_version_fencing():
    uuid = "f5a9d4e1-73c5-4812-b964-f3e7b85a1905"
    with patch.object(grpc_client, "add_user", return_value=True):
        # Sync version 5
        res = client.post(
            "/v1/clients/sync",
            json={"client_id": uuid, "desired_state": "active", "version": 5},
            headers=VALID_HEADERS,
        )
        assert res.status_code == 200
        assert res.json()["result"] == "applied"
        assert res.json()["version"] == 5

    # Try sync older version 4 (should be fenced/ignored)
    with patch.object(grpc_client, "remove_user", return_value=True) as mock_remove:
        res = client.post(
            "/v1/clients/sync",
            json={"client_id": uuid, "desired_state": "disabled", "version": 4},
            headers=VALID_HEADERS,
        )
        assert res.status_code == 200
        assert res.json()["result"] == "already_newer"
        assert res.json().get("fenced") is True
        assert mock_remove.call_count == 0  # Not executed!


def test_client_delete_version_fencing():
    """F09: Versioned DELETE endpoint rejects stale delete requests and applies valid versions."""
    uuid = "b3c9d4e1-73c5-4812-b964-f3e7b85a1903"

    # Set up client at version 10
    client_store.add_client(uuid, version=10)

    # Stale delete with version 8 -> rejected with already_newer and fenced=True
    with patch.object(grpc_client, "remove_user", return_value=True) as mock_remove:
        res_stale = client.delete(f"/v1/clients/{uuid}?version=8", headers=VALID_HEADERS)
        assert res_stale.status_code == 200
        data_stale = res_stale.json()
        assert data_stale["status"] == "ok"
        assert data_stale["result"] == "already_newer"
        assert data_stale["fenced"] is True
        assert data_stale["version"] == 10
        assert mock_remove.call_count == 0
        assert uuid in client_store.load_clients()

    # Valid delete with version 10 -> applied
    with patch.object(grpc_client, "remove_user", return_value=True) as mock_remove:
        res_valid = client.delete(f"/v1/clients/{uuid}?version=10", headers=VALID_HEADERS)
        assert res_valid.status_code == 200
        data_valid = res_valid.json()
        assert data_valid["status"] == "ok"
        assert data_valid["result"] == "applied"
        assert data_valid["action"] == "deleted"
        assert data_valid["fenced"] is False
        assert mock_remove.call_count == 2
        assert uuid not in client_store.load_clients()


def test_client_tombstone_prevents_stale_resurrection():
    """P0: Tombstones on DELETE prevent older out-of-order syncs from resurrecting deleted clients."""
    uuid = "c4d9d4e1-73c5-4812-b964-f3e7b85a1904"
    client_store.add_client(uuid, version=5)

    # Delete client at version 5 -> creates persistent tombstone at version 5
    with patch.object(grpc_client, "remove_user", return_value=True):
        res = client.delete(f"/v1/clients/{uuid}?version=5", headers=VALID_HEADERS)
        assert res.status_code == 200
        assert res.json()["result"] == "applied"
        assert uuid not in client_store.load_clients()

    # Stale sync at version 5 or lower must be fenced and not resurrect client
    with patch.object(grpc_client, "add_user", return_value=True) as mock_add:
        res_stale = client.post(
            "/v1/clients/sync",
            json={"client_id": uuid, "desired_state": "active", "version": 5},
            headers=VALID_HEADERS,
        )
        assert res_stale.status_code == 200
        assert res_stale.json()["result"] == "already_newer"
        assert res_stale.json().get("fenced") is True
        assert mock_add.call_count == 0
        assert uuid not in client_store.load_clients()

    # Newer sync at version 6 successfully resurrects client
    with patch.object(grpc_client, "add_user", return_value=True) as mock_add:
        res_new = client.post(
            "/v1/clients/sync",
            json={"client_id": uuid, "desired_state": "active", "version": 6},
            headers=VALID_HEADERS,
        )
        assert res_new.status_code == 200
        assert res_new.json()["result"] == "applied"
        assert mock_add.call_count > 0
        assert uuid in client_store.load_clients()


def test_dynamic_inbound_discovery(tmp_path):
    """F10: Discovers VLESS inbounds strictly filtering by managed namespaced tags (just1k-wl-*)."""
    # 1. Unset explicit override
    with patch.dict(os.environ, {}, clear=False):
        if "XRAY_INBOUND_TAGS" in os.environ:
            del os.environ["XRAY_INBOUND_TAGS"]

        # Create mock config.json with mix of managed and unmanaged inbounds
        cfg_file = tmp_path / "xray_config.json"
        mock_config = {
            "inbounds": [
                {"tag": "api-grpc", "protocol": "dokodemo-door"},
                {"tag": "just1k-wl-default", "protocol": "vless"},
                {"tag": "just1k-wl-inbound-de", "protocol": "vless"},
                {"tag": "inbound-nl", "protocol": "vless"},
                {"tag": "custom-unmanaged-vless", "protocol": "vless"},
                {"tag": "direct", "protocol": "freedom"},
            ]
        }
        cfg_file.write_text(json.dumps(mock_config), encoding="utf-8")

        # Create mock relays.json
        relays_file = tmp_path / "relays.json"
        mock_relays = [
            {"code": "fr", "inbound_tag": "just1k-wl-inbound-fr"},
            {"code": "us", "inbound_tag": "inbound-us"},
        ]
        relays_file.write_text(json.dumps(mock_relays), encoding="utf-8")

        with patch("app.XRAY_CONFIG_PATH", cfg_file):
            with patch("app.RELAYS_FILE_PATH", relays_file):
                inbounds = get_target_inbounds()
                # Must include ONLY managed just1k-wl-* inbounds with default first, and exclude unmanaged tags
                assert "just1k-wl-default" in inbounds
                assert "just1k-wl-inbound-de" in inbounds
                assert "just1k-wl-inbound-fr" in inbounds
                assert "inbound-nl" not in inbounds
                assert "inbound-us" not in inbounds
                assert "custom-unmanaged-vless" not in inbounds
                assert "api-grpc" not in inbounds
                assert "direct" not in inbounds
                assert inbounds[0] == "just1k-wl-default"


def test_secret_base_path_managed_tag_only(tmp_path):
    """F11: Discovers secret base path strictly matching managed tags (just1k-wl-* or inbound-default)."""
    cfg_file = tmp_path / "xray_config_secret.json"
    state_file = tmp_path / "state.json"

    # Case 1: config.json where first inbound is third-party unmanaged, second is managed just1k-wl-default
    mock_config = {
        "inbounds": [
            {
                "tag": "unmanaged-inbound",
                "streamSettings": {
                    "xhttpSettings": {"path": "/unmanaged_path/client"}
                },
            },
            {
                "tag": "just1k-wl-default",
                "streamSettings": {
                    "xhttpSettings": {"path": "/just1k_secret_path/client"}
                },
            },
        ]
    }
    cfg_file.write_text(json.dumps(mock_config), encoding="utf-8")

    with patch("app.XRAY_CONFIG_PATH", cfg_file):
        with patch("app.STATE_FILE_PATH", state_file):
            secret_path = get_secret_base_path()
            assert secret_path == "/just1k_secret_path"

    # Case 2: state.json is present -> state.json takes top priority
    state_file.write_text(json.dumps({"secret_base_path": "/canonical_state_path"}), encoding="utf-8")
    with patch("app.XRAY_CONFIG_PATH", cfg_file):
        with patch("app.STATE_FILE_PATH", state_file):
            assert get_secret_base_path() == "/canonical_state_path"


def test_health_includes_secret_base_path():
    with patch.object(grpc_client, "is_healthy", return_value=True):
        with patch.object(
            epoch_manager,
            "get_process_and_epoch",
            return_value=(100, 1000, "boot-1", "epoch_1"),
        ):
            res = client.get("/v1/health", headers=VALID_HEADERS)
            assert res.status_code == 200
            data = res.json()
            assert "secret_base_path" in data
            assert "sync_status" in data
            assert "synchronized" in data


def test_health_fail_closed_when_grpc_down():
    with patch.object(grpc_client, "is_healthy", return_value=False):
        with patch.object(
            epoch_manager,
            "get_process_and_epoch",
            return_value=(100, 1000, "boot-1", "epoch_1"),
        ):
            res = client.get("/v1/health", headers=VALID_HEADERS)
            assert res.status_code == 503
            data = res.json()
            assert data["status"] == "error"
            assert data["xray_running"] is False
            assert data["grpc_ok"] is False
            assert data["node_epoch"] is None


def test_health_fail_closed_when_process_unreadable():
    with patch.object(grpc_client, "is_healthy", return_value=True):
        with patch.object(
            epoch_manager,
            "get_process_and_epoch",
            return_value=(None, None, "boot-1", None),
        ):
            res = client.get("/v1/health", headers=VALID_HEADERS)
            assert res.status_code == 503
            data = res.json()
            assert data["status"] == "error"
            assert data["xray_running"] is False
            assert data["node_epoch"] is None


def test_traffic_snapshot_fail_closed_when_grpc_down():
    with patch.object(grpc_client, "is_healthy", return_value=False):
        res = client.get("/v1/traffic/snapshot", headers=VALID_HEADERS)
        assert res.status_code == 503
        assert "not available" in res.json().get("detail", "").lower()


def test_traffic_snapshot_fail_closed_when_process_unreadable():
    with patch.object(grpc_client, "is_healthy", return_value=True):
        with patch.object(
            epoch_manager,
            "get_process_and_epoch",
            return_value=(None, None, None, None),
        ):
            res = client.get("/v1/traffic/snapshot", headers=VALID_HEADERS)
            assert res.status_code == 503
            assert "unavailable" in res.json().get("detail", "").lower()

