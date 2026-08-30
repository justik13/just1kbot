import os
from unittest.mock import patch
from fastapi.testclient import TestClient

# Ensure XRAY_API_KEY is set before importing app
os.environ["XRAY_API_KEY"] = "test-secret-key-12345"

from app import app, grpc_client, epoch_manager

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
        with patch.object(epoch_manager, "get_process_and_epoch", return_value=(1234, 100, "boot_1", "epoch_123")):
            res = client.get("/v1/health", headers=VALID_HEADERS)
            assert res.status_code == 200
            data = res.json()
            assert data["status"] == "ok"
            assert data["xray_running"] is True
            assert data["grpc_ok"] is True
            assert data["node_epoch"] == "epoch_123"
            assert data["boot_id"] == "boot_1"
            assert data["starttime"] == 100


def test_traffic_snapshot():
    mock_stats = {
        "a2b9d4e1-73c5-4812-b964-f3e7b85a1902": {"uplink": 1024, "downlink": 2048}
    }
    with patch.object(grpc_client, "get_users_stats", return_value=mock_stats):
        with patch.object(epoch_manager, "get_process_and_epoch", return_value=(1234, 100, "boot_1", "epoch_test_123")):
            res = client.get("/v1/traffic/snapshot", headers=VALID_HEADERS)
            assert res.status_code == 200
            data = res.json()
            assert data["node_epoch"] == "epoch_test_123"
            assert data["boot_id"] == "boot_1"
            assert data["starttime"] == 100
            assert data["users"] == mock_stats


def test_traffic_snapshot_epoch_mismatch_retry_and_recovery():
    mock_stats = {"a2b9d4e1-73c5-4812-b964-f3e7b85a1902": {"uplink": 100, "downlink": 200}}
    with patch.object(grpc_client, "get_users_stats", return_value=mock_stats):
        with patch.object(
            epoch_manager,
            "get_process_and_epoch",
            side_effect=[
                (1234, 100, "boot_1", "epoch_1"),
                (1235, 200, "boot_1", "epoch_2"),
                (1235, 200, "boot_1", "epoch_2"),
                (1235, 200, "boot_1", "epoch_2"),
            ],
        ):
            res = client.get("/v1/traffic/snapshot", headers=VALID_HEADERS)
            assert res.status_code == 200
            data = res.json()
            assert data["node_epoch"] == "epoch_2"
            assert data["boot_id"] == "boot_1"
            assert data["starttime"] == 200
            assert data["users"] == mock_stats






def test_client_sync_active():
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
        assert data["client_id"] == uuid
        assert data["state"] == "active"
        # Should be called for each configured target inbound ("inbound-de", "inbound-nl")
        assert mock_add.call_count == 2


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
        assert data["client_id"] == uuid
        assert data["state"] == "disabled"
        assert mock_remove.call_count == 2


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
    with patch.object(grpc_client, "remove_user", return_value=True) as mock_remove:
        res = client.delete(f"/v1/clients/{uuid}", headers=VALID_HEADERS)
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ok"
        assert data["client_id"] == uuid
        assert data["action"] == "deleted"
        assert mock_remove.call_count == 2


def test_client_delete_invalid_uuid():
    res = client.delete("/v1/clients/invalid-uuid", headers=VALID_HEADERS)
    assert res.status_code == 422
