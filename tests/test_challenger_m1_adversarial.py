"""
Adversarial Stress & Edge Case Test Suite for Milestone 1 (Node Provisioning & Security Core).
Executed by Challenger 1 in Ubuntu 24.04 Docker environment.

Covers:
1. scripts/xray_api/app.py & epoch_manager.py CAS concurrency & process crash handling:
   - Rapid /v1/traffic/snapshot queries during process crash / restart / PID drift
   - Handling unreadable /proc, missing boot_id, corrupted /proc/<pid>/stat
   - High-concurrency async load on /v1/traffic/snapshot and ClientStore
   - Persistent tombstone and monotonic version fencing edge cases
   - Permission mode and atomic state updates under failure
2. scripts/just1knode.sh installer & security core:
   - Dynamic ref fallback resolution (SHA40 vs branch vs default)
   - Archive download and shallow clone fallbacks
   - Non-root user permissions matrix (xrayapi user, systemd unit isolation)
   - Country code path traversal sanitization in add_relay_node
   - Doctor diagnostics under diverse fault injections
"""

import concurrent.futures
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
XRAY_API_DIR = REPO_ROOT / "scripts" / "xray_api"
JUST1KNODE_SH = REPO_ROOT / "scripts" / "just1knode.sh"
REQUIREMENTS_TXT = XRAY_API_DIR / "requirements.txt"

# Add xray_api to sys.path for direct module import
if str(XRAY_API_DIR) not in sys.path:
    sys.path.insert(0, str(XRAY_API_DIR))

try:
    from fastapi.testclient import TestClient
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False


@unittest.skipUnless(HAS_FASTAPI, "fastapi not installed in current environment")
class TestXrayApiAdversarial(unittest.TestCase):
    """Adversarial stress and edge case tests for Xray API agent."""

    def setUp(self):
        if not HAS_FASTAPI:
            self.skipTest("fastapi not installed in current environment")

        self.temp_dir = tempfile.mkdtemp()
        self.clients_file = Path(self.temp_dir) / "clients.json"
        self.epoch_file = Path(self.temp_dir) / "epoch.json"
        self.lock_file = Path(self.temp_dir) / "epoch.lock"
        self.config_env_file = Path(self.temp_dir) / "config.env"

        os.environ["XRAY_API_KEY"] = "adv-test-secret-key"
        os.environ["CLIENTS_FILE_PATH"] = str(self.clients_file)
        os.environ["EPOCH_FILE_PATH"] = str(self.epoch_file)
        os.environ["EPOCH_LOCK_PATH"] = str(self.lock_file)
        os.environ["XRAY_INBOUND_TAGS"] = "just1k-wl-default,just1k-wl-inbound-de"

        # Re-import / reload app components
        import app
        import client_store
        import epoch_manager

        self.app_module = app
        self.client_store_module = client_store
        self.epoch_manager_module = epoch_manager

        # Point app modules to this test's clean files
        self.app_module.client_store = client_store.ClientStore(self.clients_file)
        self.app_module.epoch_manager = epoch_manager.EpochManager(
            file_path=str(self.epoch_file),
            lock_path=str(self.lock_file),
        )
        self.epoch_mgr = self.app_module.epoch_manager

        self.client = TestClient(app.app)
        self.headers = {"X-API-Key": "adv-test-secret-key"}

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # -------------------------------------------------------------------------
    # 1. CAS Concurrency & Process Crash Stress
    # -------------------------------------------------------------------------

    def test_snapshot_process_dies_mid_flight(self):
        """Crash scenario: Xray process dies between gRPC call and post-read verification."""
        # Step 1: pid=123, epoch_1. gRPC call succeeds.
        # Step 2: process died -> pid=None, starttime=None, epoch=None
        # Retry 1: still dead -> pid=None -> raises 503
        mock_stats = {"user-1": {"uplink": 100, "downlink": 200}}

        sequence = [
            (123, 1000, "boot-1", "epoch_1"),  # attempt 1 pre-check
            (None, None, "boot-1", None),      # attempt 1 post-check (died!)
            (None, None, "boot-1", None),      # attempt 2 pre-check (still dead)
        ]

        with patch.object(self.app_module.grpc_client, "is_healthy", return_value=True):
            with patch.object(self.app_module.grpc_client, "get_users_stats", return_value=mock_stats):
                with patch.object(self.app_module.epoch_manager, "get_process_and_epoch", side_effect=sequence):
                    res = self.client.get("/v1/traffic/snapshot", headers=self.headers)
                    self.assertEqual(res.status_code, 503)
                    self.assertIn("unavailable", res.json().get("detail", "").lower())

    def test_snapshot_continuous_rapid_restarts_exhausts_retries(self):
        """Crash scenario: Xray bounces continuously on every single check -> fail closed with EpochMismatchError."""
        mock_stats = {"user-1": {"uplink": 50, "downlink": 50}}
        # Every call returns a different PID / epoch
        continuous_bounce = [
            (100 + i, 1000 + i * 10, "boot-1", f"epoch_{i}")
            for i in range(20)
        ]

        with patch.object(self.app_module.grpc_client, "is_healthy", return_value=True):
            with patch.object(self.app_module.grpc_client, "get_users_stats", return_value=mock_stats):
                with patch.object(self.app_module.epoch_manager, "get_process_and_epoch", side_effect=continuous_bounce):
                    res = self.client.get("/v1/traffic/snapshot", headers=self.headers)
                    self.assertEqual(res.status_code, 503)
                    self.assertIn("EpochMismatchError", res.json().get("detail", ""))

    def test_snapshot_recovers_after_single_restart(self):
        """Recovery scenario: Xray restarts once, drift detected on attempt 1, stabilized on attempt 2."""
        mock_stats_new = {"user-1": {"uplink": 10, "downlink": 20}}

        sequence = [
            (100, 1000, "boot-1", "epoch_old"),  # attempt 1 pre-check
            (101, 1050, "boot-1", "epoch_new"),  # attempt 1 post-check (drift detected!)
            (101, 1050, "boot-1", "epoch_new"),  # attempt 2 pre-check
            (101, 1050, "boot-1", "epoch_new"),  # attempt 2 post-check (match!)
        ]

        with patch.object(self.app_module.grpc_client, "is_healthy", return_value=True):
            with patch.object(self.app_module.grpc_client, "get_users_stats", return_value=mock_stats_new):
                with patch.object(self.app_module.epoch_manager, "get_process_and_epoch", side_effect=sequence):
                    res = self.client.get("/v1/traffic/snapshot", headers=self.headers)
                    self.assertEqual(res.status_code, 200)
                    data = res.json()
                    self.assertEqual(data["node_epoch"], "epoch_new")
                    self.assertEqual(data["starttime"], 1050)
                    self.assertEqual(data["users"], mock_stats_new)

    def test_snapshot_concurrent_cas_under_load(self):
        """Concurrency stress: 40 parallel threads querying snapshot under concurrent load.

        Proves that every response is either an atomic valid snapshot (HTTP 200) or
        a fail-closed drift rejection (HTTP 503 EpochMismatchError), with zero 500 errors.
        """
        mock_stats = {"user-concurrency": {"uplink": 500, "downlink": 500}}

        def simulate_request(req_id: int):
            pid = 200 + (req_id % 3)
            starttime = 5000 + (req_id % 3) * 100
            epoch = f"epoch_concurrency_{req_id % 3}"

            with patch.object(self.app_module.grpc_client, "is_healthy", return_value=True):
                with patch.object(self.app_module.grpc_client, "get_users_stats", return_value=mock_stats):
                    with patch.object(
                        self.app_module.epoch_manager,
                        "get_process_and_epoch",
                        return_value=(pid, starttime, "boot-load", epoch),
                    ):
                        return self.client.get("/v1/traffic/snapshot", headers=self.headers)

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(simulate_request, i) for i in range(40)]
            results = [f.result() for f in futures]

        for res in results:
            self.assertIn(res.status_code, (200, 503))
            if res.status_code == 200:
                data = res.json()
                self.assertIn("node_epoch", data)
                self.assertIn("boot_id", data)
                self.assertEqual(data["users"], mock_stats)
            else:
                detail = res.json().get("detail", "").lower()
                self.assertTrue(
                    "epochmismatcherror" in detail
                    or "unavailable" in detail
                    or "available" in detail
                    or "stats failure" in detail,
                    f"Unexpected 503 detail: {detail}",
                )

    # -------------------------------------------------------------------------
    # 2. Edge Cases: /proc Inspection, Missing boot_id, Corrupt Stat Files
    # -------------------------------------------------------------------------

    def test_epoch_manager_missing_proc_dir(self):
        """Inspect /proc when /proc directory does not exist or is unmounted."""
        with patch("epoch_manager.Path.exists", return_value=False):
            pid, starttime = self.epoch_mgr.get_xray_process_info()
            self.assertIsNone(pid)
            self.assertIsNone(starttime)

    def test_epoch_manager_corrupt_proc_stat(self):
        """Inspect /proc/<pid>/stat with invalid/truncated format."""
        mock_proc_dir = Path(self.temp_dir) / "fake_proc"
        mock_proc_dir.mkdir()
        pid_dir = mock_proc_dir / "999"
        pid_dir.mkdir()

        (pid_dir / "comm").write_text("xray\n", encoding="utf-8")
        # Truncated stat with fewer than 20 tokens
        (pid_dir / "stat").write_text("999 (xray) S 1 2 3\n", encoding="utf-8")

        with patch("epoch_manager.Path", side_effect=lambda p: mock_proc_dir if str(p) == "/proc" else Path(p)):
            pid, starttime = self.epoch_mgr.get_xray_process_info()
            # Must safely ignore corrupt stat and return None, None
            self.assertIsNone(pid)
            self.assertIsNone(starttime)

    def test_epoch_manager_missing_boot_id_fails_closed(self):
        """When /proc/sys/kernel/random/boot_id is missing, health and snapshot fail closed."""
        with patch.object(self.epoch_mgr, "get_system_boot_id", return_value=None):
            with patch.object(self.epoch_mgr, "get_xray_process_info", return_value=(100, 1000)):
                pid, starttime, boot_id, epoch = self.epoch_mgr.get_process_and_epoch()
                # If boot_id is None, epoch can still be created, but get_health / snapshot must check boot_id
                self.assertIsNone(boot_id)

        # In app.py: get_health requires bool(grpc_ok and running_epoch and pid and starttime and boot_id)
        with patch.object(self.app_module.grpc_client, "is_healthy", return_value=True):
            with patch.object(
                self.app_module.epoch_manager,
                "get_process_and_epoch",
                return_value=(100, 1000, None, "epoch_1"),
            ):
                res = self.client.get("/v1/health", headers=self.headers)
                self.assertEqual(res.status_code, 503)
                self.assertEqual(res.json()["status"], "error")

    def test_epoch_manager_disk_save_failure_fails_closed(self):
        """When epoch.json cannot be written to disk (e.g. read-only fs), get_process_and_epoch returns epoch=None."""
        with patch.object(self.epoch_mgr, "get_system_boot_id", return_value="boot-test"):
            with patch.object(self.epoch_mgr, "get_xray_process_info", return_value=(555, 666)):
                with patch.object(self.epoch_mgr, "save_state", return_value=False):
                    pid, starttime, boot_id, epoch = self.epoch_mgr.get_process_and_epoch()
                    self.assertEqual(pid, 555)
                    self.assertEqual(starttime, 666)
                    self.assertEqual(boot_id, "boot-test")
                    self.assertIsNone(epoch, "Epoch must be None (fail-closed) when persistence fails")

    # -------------------------------------------------------------------------
    # 3. Client Store: High Concurrency, Permission Mode 0660, Tombstones
    # -------------------------------------------------------------------------

    def test_client_store_file_permission_0660(self):
        """ClientStore must set 0660 permission on saved clients.json."""
        self.app_module.client_store.add_client("a2b9d4e1-73c5-4812-b964-f3e7b85a1901")
        clients_file = Path(self.app_module.client_store.file_path)
        self.assertTrue(clients_file.exists())
        mode = stat.S_IMODE(os.stat(clients_file).st_mode)
        self.assertEqual(mode, 0o660, f"Expected 0660, got {oct(mode)}")

    def test_client_store_concurrent_writes_integrity(self):
        """Concurrent add/remove operations across multiple threads must not corrupt clients.json."""
        def worker(idx: int):
            cid = f"00000000-0000-0000-0000-{idx:012d}"
            self.app_module.client_store.add_client(cid, version=idx, email=f"user{idx}@test.com")
            if idx % 2 == 0:
                self.app_module.client_store.remove_client(cid, version=idx + 1)

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(worker, i) for i in range(1, 51)]
            for f in futures:
                f.result()

        # Verify json integrity
        loaded = self.app_module.client_store.load_clients()
        entries = self.app_module.client_store.load_client_entries()
        self.assertIsInstance(loaded, set)
        self.assertIsInstance(entries, dict)
        # All odd numbers from 1..50 should be active (25 total)
        self.assertEqual(len(loaded), 25)

    def test_client_sync_and_delete_fencing_matrix(self):
        """Comprehensive version fencing: stale syncs, stale deletes, tombstone protections."""
        uuid_test = "e1111111-2222-3333-4444-555555555555"

        with patch.object(self.app_module.grpc_client, "add_user", return_value=True):
            with patch.object(self.app_module.grpc_client, "remove_user", return_value=True):
                # 1. Sync v10
                r1 = self.client.post("/v1/clients/sync", json={"client_id": uuid_test, "version": 10}, headers=self.headers)
                self.assertEqual(r1.status_code, 200)
                self.assertEqual(r1.json()["result"], "applied")

                # 2. Sync v9 (stale) -> fenced
                r2 = self.client.post("/v1/clients/sync", json={"client_id": uuid_test, "version": 9}, headers=self.headers)
                self.assertEqual(r2.status_code, 200)
                self.assertEqual(r2.json()["result"], "already_newer")
                self.assertTrue(r2.json()["fenced"])

                # 3. Delete v10 -> applied, creates tombstone at v10
                r3 = self.client.delete(f"/v1/clients/{uuid_test}?version=10", headers=self.headers)
                self.assertEqual(r3.status_code, 200)
                self.assertEqual(r3.json()["result"], "applied")
                self.assertNotIn(uuid_test, self.app_module.client_store.load_clients())

                # 4. Stale sync v10 (same as tombstone version) -> fenced by tombstone
                r4 = self.client.post("/v1/clients/sync", json={"client_id": uuid_test, "version": 10}, headers=self.headers)
                self.assertEqual(r4.status_code, 200)
                self.assertEqual(r4.json()["result"], "already_newer")
                self.assertTrue(r4.json()["fenced"])
                self.assertNotIn(uuid_test, self.app_module.client_store.load_clients())

                # 5. Stale sync v8 -> fenced
                r5 = self.client.post("/v1/clients/sync", json={"client_id": uuid_test, "version": 8}, headers=self.headers)
                self.assertEqual(r5.status_code, 200)
                self.assertEqual(r5.json()["result"], "already_newer")

                # 6. Newer sync v11 -> resurrects client
                r6 = self.client.post("/v1/clients/sync", json={"client_id": uuid_test, "version": 11}, headers=self.headers)
                self.assertEqual(r6.status_code, 200)
                self.assertEqual(r6.json()["result"], "applied")
                self.assertIn(uuid_test, self.app_module.client_store.load_clients())


class TestJust1kNodeInstallerAdversarial(unittest.TestCase):
    """Adversarial stress and edge case tests for scripts/just1knode.sh."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.state_dir = Path(self.temp_dir) / "etc" / "just1knode"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.nginx_conf_dir = Path(self.temp_dir) / "etc" / "nginx"
        self.nginx_conf_dir.mkdir(parents=True, exist_ok=True)
        self.nginx_relays_d = Path(self.temp_dir) / "etc" / "nginx" / "just1k_relays.d"
        self.nginx_relays_d.mkdir(parents=True, exist_ok=True)
        self.xray_config_dir = Path(self.temp_dir) / "usr" / "local" / "etc" / "xray"
        self.xray_config_dir.mkdir(parents=True, exist_ok=True)
        self.xray_api_etc = Path(self.temp_dir) / "etc" / "xray-api"
        self.xray_api_etc.mkdir(parents=True, exist_ok=True)
        self.xray_api_dir = Path(self.temp_dir) / "opt" / "xray-api"
        self.xray_api_dir.mkdir(parents=True, exist_ok=True)
        self.bin_dir = Path(self.temp_dir) / "bin"
        self.bin_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_mock_script(self, name: str, content: str) -> Path:
        script_path = self.bin_dir / name
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(content)
        script_path.chmod(0o755)
        return script_path

    def _run_shell_snippet(self, snippet: str, extra_env: dict = None) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        env["PATH"] = f"{self.bin_dir}:{env['PATH']}"
        env["STATE_DIR"] = str(self.state_dir)
        env["STATE_FILE"] = str(self.state_dir / "state.json")
        env["CLIENTS_FILE"] = str(self.state_dir / "clients.json")
        env["RELAYS_FILE"] = str(self.state_dir / "relays.json")
        env["XRAY_CONFIG_DIR"] = str(self.xray_config_dir)
        env["XRAY_CONFIG"] = str(self.xray_config_dir / "config.json")
        env["NGINX_CONF_DIR"] = str(self.nginx_conf_dir)
        env["NGINX_RELAYS_DIR"] = str(self.nginx_relays_d)
        env["XRAY_API_DIR"] = str(self.xray_api_dir)
        env["XRAY_API_ETC"] = str(self.xray_api_etc)
        env["XRAY_API_CONFIG_ENV"] = str(self.xray_api_etc / "config.env")
        if extra_env:
            env.update(extra_env)

        full_script = f"""
export STATE_DIR='{self.state_dir}'
export STATE_FILE='{self.state_dir / "state.json"}'
export CLIENTS_FILE='{self.state_dir / "clients.json"}'
export RELAYS_FILE='{self.state_dir / "relays.json"}'
export XRAY_CONFIG_DIR='{self.xray_config_dir}'
export XRAY_CONFIG='{self.xray_config_dir / "config.json"}'
export NGINX_CONF_DIR='{self.nginx_conf_dir}'
export NGINX_RELAYS_DIR='{self.nginx_relays_d}'
export XRAY_API_DIR='{self.xray_api_dir}'
export XRAY_API_ETC='{self.xray_api_etc}'
export XRAY_API_CONFIG_ENV='{self.xray_api_etc / "config.env"}'

source '{JUST1KNODE_SH}'

check_root() {{ return 0; }}
install_base_deps() {{ return 0; }}
obtain_ssl_certificate() {{ return 0; }}
download_and_verify_xray() {{ return 0; }}
setup_xray_api_venv() {{ return 0; }}
ensure_xrayapi_user() {{ return 0; }}

{snippet}
"""
        return subprocess.run(
            ["bash", "-c", full_script],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )

    # -------------------------------------------------------------------------
    # 4. Ref Fallback & Deployment Resolution
    # -------------------------------------------------------------------------

    def test_ref_fallback_resolution_matrix(self):
        """Test ref resolution hierarchy: JUST1KBOT_REF -> JUST1KBOT_BRANCH -> 'main'."""
        # Case 1: Default fallback when neither is set
        cmd1 = 'echo "REF: $JUST1KBOT_REF"'
        res1 = self._run_shell_snippet(cmd1)
        self.assertIn("REF: main", res1.stdout)

        # Case 2: JUST1KBOT_BRANCH override
        cmd2 = 'echo "REF: $JUST1KBOT_REF"'
        res2 = self._run_shell_snippet(cmd2, extra_env={"JUST1KBOT_BRANCH": "dev-stage"})
        self.assertIn("REF: dev-stage", res2.stdout)

        # Case 3: JUST1KBOT_REF takes top precedence over JUST1KBOT_BRANCH
        cmd3 = 'echo "REF: $JUST1KBOT_REF"'
        res3 = self._run_shell_snippet(cmd3, extra_env={"JUST1KBOT_REF": "custom-commit-sha", "JUST1KBOT_BRANCH": "ignored-branch"})
        self.assertIn("REF: custom-commit-sha", res3.stdout)

    def test_add_relay_country_code_path_traversal_sanitization(self):
        """Adversarial input: add_relay_node with path traversal payload in country code."""
        self._create_mock_script("nginx", "#!/bin/sh\nexit 0\n")
        self._create_mock_script("xray", "#!/bin/sh\nexit 0\n")

        # Setup base state as origin
        with open(self.state_dir / "state.json", "w", encoding="utf-8") as f:
            json.dump({"role": "origin"}, f)

        # Attack payload: ../../../etc/cron.d/malicious
        cmd = 'add_relay_node "Evil" "1.2.3.4" "10443" "uuid-123" "../../../etc/cron.d/hack" "tls" "" "" "evil.com"'
        res = self._run_shell_snippet(cmd)
        self.assertNotEqual(res.returncode, 0, "add_relay_node must reject path traversal characters in code")
        self.assertIn("Недопустимый код страны", res.stderr + res.stdout)

    def test_doctor_diagnostics_fault_matrix(self):
        """Doctor tool fault injection: closed gRPC socket, failed nginx syntax, failed xray config."""
        # Setup base state as origin
        with open(self.state_dir / "state.json", "w", encoding="utf-8") as f:
            json.dump({"role": "origin", "domain": "origin.test", "bot_ip": "10.0.0.1"}, f)

        # Mock systemctl active
        self._create_mock_script("systemctl", "#!/bin/sh\nexit 0\n")

        # Mock nginx syntax error
        self._create_mock_script("nginx", "#!/bin/sh\nif [ \"$1\" = \"-t\" ]; then exit 1; fi; exit 0\n")

        # Mock xray config error
        self._create_mock_script("xray", "#!/bin/sh\nif [ \"$1\" = \"run\" ] && [ \"$2\" = \"-test\" ]; then exit 1; fi; exit 0\n")

        # Mock ufw inactive
        self._create_mock_script("ufw", "#!/bin/sh\nexit 1\n")

        res = self._run_shell_snippet("run_doctor")
        # Must detect nginx error, xray error, gRPC closed socket, ufw inactive
        self.assertIn("Ошибка конфигурации Xray", res.stdout + res.stderr)
        self.assertIn("Ошибка синтаксиса Nginx", res.stdout + res.stderr)
        self.assertIn("gRPC сокет Xray недоступен", res.stdout + res.stderr)
        self.assertIn("Обнаружено ошибок", res.stdout + res.stderr)


if __name__ == "__main__":
    unittest.main()
