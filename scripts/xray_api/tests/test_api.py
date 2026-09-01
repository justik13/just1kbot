import os
import tempfile
from unittest.mock import patch
from fastapi.testclient import TestClient

# Ensure XRAY_API_KEY and CLIENTS_FILE_PATH are set before importing app
tmp_clients_file = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
tmp_clients_file.close()
os.environ["XRAY_API_KEY"] = "test-secret-key-12345"
os.environ["CLIENTS_FILE_PATH"] = tmp_clients_file.name
os.environ["XRAY_INBOUND_TAGS"] = "inbound-de,inbound-nl"

from app import app, client_store, grpc_client  # noqa: E402

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
            res = client.get("/v1/traffic/snapshot", headers=VALID_HEADERS)
            assert res.status_code == 200
            data = res.json()
            assert data["users"] == mock_stats
            assert "timestamp" in data


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
        assert data["client_id"] == uuid
        assert data["action"] == "deleted"
        assert mock_remove.call_count == 2
        assert uuid not in client_store.load_clients()


def test_client_delete_invalid_uuid():
    res = client.delete("/v1/clients/invalid-uuid", headers=VALID_HEADERS)
    assert res.status_code == 422
