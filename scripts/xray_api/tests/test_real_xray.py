import json
import os
import shutil
import subprocess
import time
import pytest
from fastapi.testclient import TestClient

from app import app
from epoch_manager import EpochManager
from xray_grpc import XrayGrpcClient

XRAY_BIN = shutil.which("xray") or "/usr/local/bin/xray" or "/tmp/xray"


@pytest.mark.skipif(
    not os.path.exists(XRAY_BIN) or not os.access(XRAY_BIN, os.X_OK),
    reason="Xray binary not found or not executable",
)
def test_end_to_end_with_real_xray():
    # 1. Prepare minimal test config for Xray with API enabled
    test_config = {
        "log": {"loglevel": "warning"},
        "api": {
            "tag": "api",
            "services": ["HandlerService", "StatsService"],
        },
        "inbounds": [
            {
                "tag": "inbound-de",
                "port": 18003,
                "listen": "127.0.0.1",
                "protocol": "vless",
                "settings": {"clients": [], "decryption": "none"},
            },
            {
                "tag": "inbound-nl",
                "port": 18004,
                "listen": "127.0.0.1",
                "protocol": "vless",
                "settings": {"clients": [], "decryption": "none"},
            },
            {
                "tag": "api",
                "port": 10085,
                "listen": "127.0.0.1",
                "protocol": "dokodemo-door",
                "settings": {"address": "127.0.0.1"},
            },
        ],
        "outbounds": [{"tag": "direct", "protocol": "freedom"}],
        "routing": {
            "rules": [
                {
                    "type": "field",
                    "inboundTag": ["api"],
                    "outboundTag": "api",
                }
            ]
        },
        "stats": {},
        "policy": {
            "levels": {"0": {"statsUserUplink": True, "statsUserDownlink": True}}
        },
    }

    config_path = "/tmp/test_xray_config.json"
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(test_config, f)

    # 2. Test config validation with xray
    test_proc = subprocess.run(
        [XRAY_BIN, "run", "-test", "-config", config_path],
        capture_output=True,
        text=True,
        check=False,
    )

    assert test_proc.returncode == 0, f"Xray test failed: {test_proc.stderr}"

    # 3. Start real Xray process
    proc = subprocess.Popen([XRAY_BIN, "run", "-config", config_path])
    try:
        # Allow Xray to bind ports
        time.sleep(1.0)
        assert proc.poll() is None, "Xray process terminated unexpectedly"

        client = XrayGrpcClient(host="127.0.0.1", port=10085, timeout=3.0)
        assert client.is_healthy() is True

        # Test real AddUser on BOTH inbounds with the same UUID
        test_uuid = "a2b9d4e1-73c5-4812-b964-f3e7b85a1902"
        assert client.add_user("inbound-de", test_uuid) is True
        assert client.add_user("inbound-nl", test_uuid) is True

        # Test idempotent duplicate AddUser on both inbounds
        assert client.add_user("inbound-de", test_uuid) is True
        assert client.add_user("inbound-nl", test_uuid) is True

        # Test real RemoveUser on both inbounds
        assert client.remove_user("inbound-de", test_uuid) is True
        assert client.remove_user("inbound-nl", test_uuid) is True

        # Test idempotent RemoveUser
        assert client.remove_user("inbound-de", test_uuid) is True
        assert client.remove_user("inbound-nl", test_uuid) is True

        # Test stats query aggregation logic
        stats = client.get_users_stats(reset=False)
        assert isinstance(stats, dict)

        # Test EpochManager with real running Xray process
        epoch_mgr = EpochManager(file_path="/tmp/test_epoch.json")
        pid, starttime = epoch_mgr.get_xray_process_info()
        assert pid == proc.pid
        assert starttime is not None
        epoch = epoch_mgr.get_current_running_epoch()
        assert epoch is not None and epoch.startswith("epoch_")

        # Test FastAPI end-to-end
        import app as app_module
        app_module.epoch_manager = epoch_mgr
        os.environ["XRAY_API_KEY"] = "live-test-secret"
        os.environ["XRAY_GRPC_PORT"] = "10085"
        api_client = TestClient(app)
        headers = {"X-API-Key": "live-test-secret"}

        # Health endpoint when running -> 200 OK, grpc_ok=True
        health_resp = api_client.get("/v1/health", headers=headers)
        assert health_resp.status_code == 200
        health_data = health_resp.json()
        assert health_data["status"] == "ok"
        assert health_data["xray_running"] is True
        assert health_data["grpc_ok"] is True
        assert health_data["node_epoch"] == epoch

        # Sync client -> provisions on both inbound-de and inbound-nl
        sync_resp = api_client.post(
            "/v1/clients/sync",
            json={"client_id": test_uuid, "desired_state": "active"},
            headers=headers,
        )
        assert sync_resp.status_code == 200
        assert sync_resp.json()["status"] == "ok"

        snap_resp = api_client.get("/v1/traffic/snapshot", headers=headers)
        assert snap_resp.status_code == 200
        assert "node_epoch" in snap_resp.json()

        del_resp = api_client.delete(f"/v1/clients/{test_uuid}", headers=headers)
        assert del_resp.status_code == 200
        assert del_resp.json()["status"] == "ok"
    finally:
        proc.terminate()
        proc.wait(timeout=5.0)

    # Verify fail-closed health after Xray stops
    stopped_health = api_client.get("/v1/health", headers=headers)
    assert stopped_health.status_code == 503
    stopped_data = stopped_health.json()
    assert stopped_data["status"] == "error"
    assert stopped_data["xray_running"] is False
    assert stopped_data["node_epoch"] is None

    if os.path.exists(config_path):
        os.remove(config_path)
    if os.path.exists("/tmp/test_epoch.json"):
        os.remove("/tmp/test_epoch.json")
