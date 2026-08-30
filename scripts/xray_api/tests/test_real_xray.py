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

        # Test real AddUser
        test_uuid = "a2b9d4e1-73c5-4812-b964-f3e7b85a1902"
        assert client.add_user("inbound-de", test_uuid) is True

        # Test idempotent duplicate AddUser
        assert client.add_user("inbound-de", test_uuid) is True

        # Test real RemoveUser
        assert client.remove_user("inbound-de", test_uuid) is True

        # Test idempotent RemoveUser
        assert client.remove_user("inbound-de", test_uuid) is True

        # Test stats query
        stats = client.get_users_stats(reset=False)
        assert isinstance(stats, dict)

        # Test EpochManager with real Xray process
        epoch_mgr = EpochManager(file_path="/tmp/test_epoch.json")
        pid, starttime = epoch_mgr.get_xray_process_info()
        assert pid == proc.pid
        assert starttime is not None
        epoch = epoch_mgr.get_current_epoch()
        assert epoch.startswith("epoch_")

        # Test FastAPI end-to-end
        os.environ["XRAY_API_KEY"] = "live-test-secret"
        os.environ["XRAY_GRPC_PORT"] = "10085"
        api_client = TestClient(app)
        headers = {"X-API-Key": "live-test-secret"}

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
        if os.path.exists(config_path):
            os.remove(config_path)
