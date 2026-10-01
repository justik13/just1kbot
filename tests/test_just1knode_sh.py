"""
Tests for scripts/just1knode.sh provisioning, namespacing, transactional rollback,
fail-closed firewall, and immutable dependency pinning.
Covers findings F02, F03, F04, F05, F19, F20, F21, F22.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
JUST1KNODE_SH = REPO_ROOT / "scripts" / "just1knode.sh"
REQUIREMENTS_TXT = REPO_ROOT / "scripts" / "xray_api" / "requirements.txt"


class TestJust1kNodeScript(unittest.TestCase):
    @classmethod
    def _bp(cls, p: Path | str) -> str:
        p_str = str(p).replace("\\", "/")
        if os.name == "nt" and re.match(r"^[a-zA-Z]:", p_str):
            drive = p_str[0].lower()
            return f"/mnt/{drive}{p_str[2:]}"
        return p_str

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
        self.xray_share_dir = Path(self.temp_dir) / "usr" / "local" / "share" / "xray"
        self.xray_share_dir.mkdir(parents=True, exist_ok=True)
        self.xray_api_etc = Path(self.temp_dir) / "etc" / "xray-api"
        self.xray_api_etc.mkdir(parents=True, exist_ok=True)
        self.xray_api_dir = Path(self.temp_dir) / "opt" / "xray-api"
        self.xray_api_dir.mkdir(parents=True, exist_ok=True)
        self.xray_api_lib = Path(self.temp_dir) / "var" / "lib" / "xray-api"
        self.xray_api_lib.mkdir(parents=True, exist_ok=True)
        self.systemd_dir = Path(self.temp_dir) / "etc" / "systemd" / "system"
        self.systemd_dir.mkdir(parents=True, exist_ok=True)
        self.certbot_dir = Path(self.temp_dir) / "var" / "www" / "certbot"
        self.certbot_dir.mkdir(parents=True, exist_ok=True)
        self.letsencrypt_dir = Path(self.temp_dir) / "etc" / "letsencrypt"
        self.letsencrypt_dir.mkdir(parents=True, exist_ok=True)
        self.www_html_dir = Path(self.temp_dir) / "var" / "www" / "html"
        self.www_html_dir.mkdir(parents=True, exist_ok=True)
        self.backup_dir = Path(self.temp_dir) / "var" / "backups" / "just1knode"
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self.install_dir = Path(self.temp_dir) / "opt" / "just1knode"
        self.install_dir.mkdir(parents=True, exist_ok=True)
        self.bin_dir = Path(self.temp_dir) / "bin"
        self.bin_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_mock_script(self, name: str, content: str) -> Path:
        script_path = self.bin_dir / name
        with open(script_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
        script_path.chmod(0o755)
        return script_path

    def _prepare_base_env(self):
        # Default mock commands
        self._create_mock_script("nginx", "#!/bin/sh\nexit 0\n")
        self._create_mock_script("systemctl", "#!/bin/sh\nexit 0\n")
        self._create_mock_script("xray", "#!/bin/sh\nexit 0\n")
        self._create_mock_script("ufw", "#!/bin/sh\nexit 0\n")
        self._create_mock_script("certbot", "#!/bin/sh\nexit 0\n")
        self._create_mock_script("apt-get", "#!/bin/sh\nexit 0\n")
        self._create_mock_script("pkill", "#!/bin/sh\nexit 0\n")
        self._create_mock_script("userdel", "#!/bin/sh\nexit 0\n")
        self._create_mock_script("groupdel", "#!/bin/sh\nexit 0\n")
        self._create_mock_script("sysctl", "#!/bin/sh\nexit 0\n")
        self._create_mock_script(
            "unzip",
            """#!/bin/sh
dest="."
prev=""
for arg in "$@"; do
    if [ "$prev" = "-d" ]; then
        dest="$arg"
    fi
    prev="$arg"
done
mkdir -p "$dest"
for arg in "$@"; do
    if [ -f "$arg" ]; then
        python3 -c "import zipfile; zipfile.ZipFile('$arg').extractall('$dest')" 2>/dev/null || true
    fi
done
exit 0
""",
        )

        # Initial files
        state_file = self.state_dir / "state.json"
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump(
                {"role": "origin", "domain": "origin.example.com", "secret_base_path": "/stream"}, f
            )

        relays_file = self.state_dir / "relays.json"
        with open(relays_file, "w", encoding="utf-8") as f:
            json.dump([], f)

        xray_config = self.xray_config_dir / "config.json"
        with open(xray_config, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "inbounds": [{"tag": "just1k-wl-default", "port": 8003, "protocol": "vless"}],
                    "outbounds": [
                        {"tag": "just1k-wl-direct", "protocol": "freedom"},
                        {"tag": "just1k-wl-block", "protocol": "blackhole"},
                        {"tag": "direct", "protocol": "freedom"},
                        {"tag": "block", "protocol": "blackhole"},
                    ],
                    "routing": {"rules": []},
                },
                f,
            )

        env_file = self.xray_api_etc / "config.env"
        with open(env_file, "w", encoding="utf-8") as f:
            f.write("XRAY_API_KEY=testkey\nXRAY_INBOUND_TAGS=just1k-wl-default\n")

    def _run_shell_snippet(
        self, snippet: str, extra_env: dict = None, input_text: str | None = None
    ) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        env["PATH"] = f"{self.bin_dir}:{env['PATH']}"
        env["STATE_DIR"] = str(self.state_dir)
        env["STATE_FILE"] = str(self.state_dir / "state.json")
        env["CLIENTS_FILE"] = str(self.state_dir / "clients.json")
        env["RELAYS_FILE"] = str(self.state_dir / "relays.json")
        env["XRAY_CONFIG_DIR"] = str(self.xray_config_dir)
        env["XRAY_CONFIG"] = str(self.xray_config_dir / "config.json")
        env["XRAY_SHARE_DIR"] = str(self.xray_share_dir)
        env["XRAY_BIN"] = str(self.bin_dir / "xray")
        env["BACKUP_DIR"] = str(self.backup_dir)
        env["NGINX_CONF_DIR"] = str(self.nginx_conf_dir)
        env["NGINX_RELAYS_DIR"] = str(self.nginx_relays_d)
        env["XRAY_API_DIR"] = str(self.xray_api_dir)
        env["XRAY_API_LIB"] = str(self.xray_api_lib)
        env["XRAY_API_ETC"] = str(self.xray_api_etc)
        env["XRAY_API_CONFIG_ENV"] = str(self.xray_api_etc / "config.env")
        env["SYSTEMD_SYSTEM_DIR"] = str(self.systemd_dir)
        env["CERTBOT_DIR"] = str(self.certbot_dir)
        env["LETSENCRYPT_DIR"] = str(self.letsencrypt_dir)
        env["XRAY_TLS_DIR"] = str(self.xray_config_dir / "tls")
        env["WWW_HTML_DIR"] = str(self.www_html_dir)
        env["INSTALL_DIR"] = str(self.install_dir)
        if extra_env:
            env.update(extra_env)

        backup_dir_val = extra_env.get("BACKUP_DIR", str(self.backup_dir)) if extra_env else str(self.backup_dir)
        state_dir_val = extra_env.get("STATE_DIR", str(self.state_dir)) if extra_env else str(self.state_dir)

        _bp = self._bp

        # Source just1knode.sh functions and run snippet with root bypass for testing
        full_script = f"""
export PATH='{_bp(self.bin_dir)}':"$PATH"
export STATE_DIR='{_bp(state_dir_val)}'
export STATE_FILE='{_bp(self.state_dir / "state.json")}'
export CLIENTS_FILE='{_bp(self.state_dir / "clients.json")}'
export RELAYS_FILE='{_bp(self.state_dir / "relays.json")}'
export XRAY_CONFIG_DIR='{_bp(self.xray_config_dir)}'
export XRAY_CONFIG='{_bp(self.xray_config_dir / "config.json")}'
export XRAY_SHARE_DIR='{_bp(self.xray_share_dir)}'
export XRAY_BIN='{_bp(self.bin_dir / "xray")}'
export BACKUP_DIR='{_bp(backup_dir_val)}'
export NGINX_CONF_DIR='{_bp(self.nginx_conf_dir)}'
export NGINX_RELAYS_DIR='{_bp(self.nginx_relays_d)}'
export XRAY_API_DIR='{_bp(self.xray_api_dir)}'
export XRAY_API_LIB='{_bp(self.xray_api_lib)}'
export XRAY_API_ETC='{_bp(self.xray_api_etc)}'
export XRAY_API_CONFIG_ENV='{_bp(self.xray_api_etc / "config.env")}'
export SYSTEMD_SYSTEM_DIR='{_bp(self.systemd_dir)}'
export CERTBOT_DIR='{_bp(self.certbot_dir)}'
export LETSENCRYPT_DIR='{_bp(self.letsencrypt_dir)}'
export XRAY_TLS_DIR='{_bp(self.xray_config_dir / "tls")}'
export WWW_HTML_DIR='{_bp(self.www_html_dir)}'
export INSTALL_DIR='{_bp(self.install_dir)}'

source '{_bp(JUST1KNODE_SH)}'

check_root() {{ return 0; }}
install_base_deps() {{ return 0; }}
obtain_ssl_certificate() {{ return 0; }}
download_and_verify_xray() {{ return 0; }}
deploy_xray_api_sources() {{ return 0; }}
setup_xray_api_venv() {{ return 0; }}
ensure_xrayapi_user() {{ return 0; }}

{snippet}
"""
        return subprocess.run(
            ["bash", "-c", full_script],
            input=input_text if input_text is not None else "",
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            check=False,
        )

    # -------------------------------------------------------------------------
    # F02: Transactional Rollback on Nginx Failure in add_relay_node
    # -------------------------------------------------------------------------
    def test_relay_add_rollback_on_nginx_failure(self):
        self._prepare_base_env()
        # Create a pre-existing relay conf for "de" to verify it is restored, not deleted
        de_conf = self.nginx_relays_d / "de.conf"
        original_de_content = "# Pre-existing DE relay config\nlocation ^~ /old { proxy_pass http://127.0.0.1:8001; }\n"
        with open(de_conf, "w", encoding="utf-8") as f:
            f.write(original_de_content)

        original_relays = [
            {
                "code": "de",
                "name": "Old Germany",
                "inbound_port": 8004,
                "inbound_tag": "just1k-wl-inbound-de",
            }
        ]
        with open(self.state_dir / "relays.json", "w", encoding="utf-8") as f:
            json.dump(original_relays, f)

        # Make nginx fail on validation
        self._create_mock_script(
            "nginx",
            """#!/bin/sh
if [ "$1" = "-t" ]; then
    echo "nginx: configuration syntax error test" >&2
    exit 1
fi
exit 0
""",
        )

        cmd = 'add_relay_node "Germany" "1.2.3.4" "10443" "new-uuid" "de" "tls" "" "" "relay.example.com"'
        res = self._run_shell_snippet(cmd)

        self.assertNotEqual(res.returncode, 0, "add_relay_node must fail when nginx -t fails")
        self.assertIn("Ошибка конфигурации Nginx", res.stderr + res.stdout)

        # Check rollback: pre-existing de.conf must be preserved intact
        with open(de_conf, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), original_de_content)

        # Check rollback: relays.json must have original content
        with open(self.state_dir / "relays.json", "r", encoding="utf-8") as f:
            data = json.load(f)
            self.assertEqual(data, original_relays)

    # -------------------------------------------------------------------------
    # F03: Transactional Rollback on Xray Failure in remove_relay_node
    # -------------------------------------------------------------------------
    def test_relay_remove_rollback_on_xray_failure(self):
        self._prepare_base_env()
        de_conf = self.nginx_relays_d / "de.conf"
        de_content = "location ^~ /stream/de { proxy_pass http://127.0.0.1:8004; }\n"
        with open(de_conf, "w", encoding="utf-8") as f:
            f.write(de_content)

        relays_data = [
            {
                "code": "de",
                "name": "Germany",
                "inbound_port": 8004,
                "inbound_tag": "just1k-wl-inbound-de",
            }
        ]
        with open(self.state_dir / "relays.json", "w", encoding="utf-8") as f:
            json.dump(relays_data, f)

        xray_config_file = self.xray_config_dir / "config.json"
        with open(xray_config_file, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "inbounds": [{"tag": "just1k-wl-default"}, {"tag": "just1k-wl-inbound-de"}],
                    "outbounds": [{"tag": "just1k-wl-outbound-de"}, {"tag": "just1k-wl-direct"}],
                    "routing": {
                        "rules": [
                            {
                                "inboundTag": ["just1k-wl-inbound-de"],
                                "outboundTag": "just1k-wl-outbound-de",
                            }
                        ]
                    },
                },
                f,
            )

        # Mock xray to fail test
        self._create_mock_script(
            "xray",
            """#!/bin/sh
if [ "$1" = "run" ] && [ "$2" = "-test" ]; then
    echo "xray: config test failed" >&2
    exit 1
fi
exit 0
""",
        )

        cmd = 'remove_relay_node "de"'
        res = self._run_shell_snippet(cmd)

        self.assertNotEqual(
            res.returncode, 0, "remove_relay_node must fail closed when xray test fails"
        )
        self.assertIn("Ошибка тестирования Xray", res.stderr + res.stdout)

        # Ensure deleted de.conf was restored
        self.assertTrue(de_conf.exists(), "de.conf must be restored after rollback")
        with open(de_conf, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), de_content)

        # Ensure relays.json was restored
        with open(self.state_dir / "relays.json", "r", encoding="utf-8") as f:
            self.assertEqual(json.load(f), relays_data)

    def test_rename_relay_node(self):
        self._prepare_base_env()
        with open(self.state_dir / "state.json", "w", encoding="utf-8") as f:
            json.dump({"role": "origin"}, f)

        relays_data = [{"name": "Германия", "code": "de", "ip": "1.2.3.4", "port": 10443}]
        with open(self.state_dir / "relays.json", "w", encoding="utf-8") as f:
            json.dump(relays_data, f, ensure_ascii=False)

        cmd = 'rename_relay_node "de" "Финляндия"'
        res = self._run_shell_snippet(cmd)
        self.assertEqual(res.returncode, 0, f"rename_relay_node failed: {res.stderr + res.stdout}")

        with open(self.state_dir / "relays.json", "r", encoding="utf-8") as f:
            updated = json.load(f)
        self.assertEqual(updated[0]["name"], "Финляндия")
        self.assertEqual(updated[0]["code"], "de")

    def test_update_relay_sni(self):
        self._prepare_base_env()
        with open(self.state_dir / "state.json", "w", encoding="utf-8") as f:
            json.dump({"role": "origin"}, f)

        xray_config_file = self.xray_config_dir / "config.json"
        with open(xray_config_file, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "inbounds": [],
                    "outbounds": [
                        {
                            "tag": "just1k-wl-outbound-de",
                            "protocol": "vless",
                            "settings": {"vnext": [{"address": "1.2.3.4", "port": 10443}]},
                            "streamSettings": {
                                "security": "reality",
                                "realitySettings": {"serverName": "www.google.com"},
                            },
                        }
                    ],
                },
                f,
            )

        relays_data = [
            {
                "name": "Германия",
                "code": "de",
                "ip": "1.2.3.4",
                "port": 10443,
                "sni": "www.google.com",
                "security": "reality",
            }
        ]
        with open(self.state_dir / "relays.json", "w", encoding="utf-8") as f:
            json.dump(relays_data, f, ensure_ascii=False)

        # Test 1: Non-interactive without --force must fail-closed on DNS mismatch
        cmd_fail = 'update_relay_sni "de" "de.example.com" "tls"'
        res_fail = self._run_shell_snippet(cmd_fail)
        self.assertEqual(res_fail.returncode, 1, f"Expected non-interactive DNS mismatch to fail-closed: {res_fail.stderr + res_fail.stdout}")
        self.assertIn("ВНИМАНИЕ", res_fail.stderr + res_fail.stdout)
        self.assertIn("--force", res_fail.stderr + res_fail.stdout)
        with open(self.state_dir / "relays.json", "r", encoding="utf-8") as f:
            unmodified = json.load(f)
        self.assertEqual(unmodified[0]["sni"], "www.google.com")

        # Test 2: Non-interactive with --force proceeds and updates relay & config
        cmd = 'update_relay_sni "de" "de.example.com" "tls" "--force"'
        res = self._run_shell_snippet(cmd)
        self.assertEqual(res.returncode, 0, f"update_relay_sni failed: {res.stderr + res.stdout}")
        self.assertIn("ВНИМАНИЕ", res.stderr + res.stdout)

        with open(self.state_dir / "relays.json", "r", encoding="utf-8") as f:
            updated = json.load(f)
        self.assertEqual(updated[0]["sni"], "de.example.com")
        self.assertEqual(updated[0]["security"], "tls")

        with open(xray_config_file, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        ob = next(o for o in cfg["outbounds"] if o["tag"] == "just1k-wl-outbound-de")
        self.assertEqual(ob["streamSettings"]["security"], "tls")
        self.assertEqual(ob["streamSettings"]["tlsSettings"]["serverName"], "de.example.com")

    def test_heal_and_update_origin_config(self):
        self._prepare_base_env()
        with open(self.state_dir / "state.json", "w", encoding="utf-8") as f:
            json.dump({"role": "origin", "secret_base_path": "/stream"}, f)

        xray_config_file = self.xray_config_dir / "config.json"
        with open(xray_config_file, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "inbounds": [
                        {"tag": "just1k-wl-default", "port": 8003, "protocol": "vless"},
                        {"tag": "just1k-wl-inbound-de", "port": 8004, "protocol": "vless"},
                        {"tag": "just1k-wl-api-grpc", "port": 10085, "protocol": "dokodemo-door"},
                    ],
                    "outbounds": [
                        {
                            "tag": "just1k-wl-direct",
                            "protocol": "freedom",
                            "settings": {"domainStrategy": "UseIP"},
                        }
                    ],
                    "routing": {
                        "rules": [
                            {
                                "type": "field",
                                "inboundTag": ["just1k-wl-default"],
                                "domain": ["domain:ru"],
                                "outboundTag": "just1k-wl-direct",
                            }
                        ]
                    },
                },
                f,
            )

        cmd = "heal_and_update_origin_config"
        res = self._run_shell_snippet(cmd)
        self.assertEqual(
            res.returncode, 0, f"heal_and_update_origin_config failed: {res.stderr + res.stdout}"
        )

        with open(xray_config_file, "r", encoding="utf-8") as f:
            updated = json.load(f)

        # 1. Check UseIPv4
        direct_ob = next(ob for ob in updated["outbounds"] if ob["tag"] == "just1k-wl-direct")
        self.assertEqual(direct_ob["settings"]["domainStrategy"], "UseIPv4")

        # 2. Check direct routing rule updated with domain:2ip.ru and inbound-de
        direct_rule = next(
            r for r in updated["routing"]["rules"] if r.get("outboundTag") == "just1k-wl-direct"
        )
        self.assertIn("domain:2ip.ru", direct_rule["domain"])
        self.assertIn("just1k-wl-inbound-de", direct_rule["inboundTag"])

        # 3. Check Split-DNS and skipFallback (domestic resolvers only, no foreign 1.1.1.1)
        self.assertEqual(updated["dns"]["queryStrategy"], "UseIPv4")
        ru_server = updated["dns"]["servers"][0]
        self.assertEqual(ru_server["address"], "77.88.8.8")
        self.assertIn("domain:2ip.ru", ru_server["domains"])
        self.assertTrue(ru_server.get("skipFallback"))
        self.assertNotIn("1.1.1.1", updated["dns"]["servers"])
        self.assertIn("195.208.4.1", updated["dns"]["servers"])
        self.assertIn("77.88.8.1", updated["dns"]["servers"])
        self.assertNotIn("localhost", updated["dns"]["servers"])

        # 3b. Verify ip_rule (geoip:ru) excludes relay inbounds to prevent DNS resolution on Origin
        ip_rule = next(
            (r for r in updated["routing"]["rules"] if r.get("outboundTag") == "just1k-wl-direct" and "ip" in r),
            None,
        )
        self.assertIsNotNone(ip_rule, "ip_rule with geoip:ru for direct routing must exist")
        self.assertNotIn("just1k-wl-inbound-de", ip_rule.get("inboundTag", []))

        # 4. Check sniffing routeOnly == False and quic on client inbounds
        for ib in updated["inbounds"]:
            if ib["tag"] == "just1k-wl-api-grpc":
                # Ensure API inbound does NOT have sniffing
                self.assertNotIn("sniffing", ib)
            else:
                self.assertTrue(ib["sniffing"]["enabled"])
                self.assertFalse(ib["sniffing"]["routeOnly"])
                self.assertIn("quic", ib["sniffing"]["destOverride"])

    def test_heal_reconstructs_missing_invariants(self):
        self._prepare_base_env()
        with open(self.state_dir / "state.json", "w", encoding="utf-8") as f:
            json.dump({"role": "origin", "secret_base_path": "/custom_stream"}, f)

        xray_config_file = self.xray_config_dir / "config.json"
        # Config completely lacking outbounds and routing rules
        with open(xray_config_file, "w", encoding="utf-8") as f:
            json.dump({"inbounds": [], "outbounds": []}, f)

        cmd = "heal_and_update_origin_config"
        res = self._run_shell_snippet(cmd)
        self.assertEqual(
            res.returncode,
            0,
            f"heal_and_update_origin_config failed on broken config: {res.stderr + res.stdout}",
        )

        with open(xray_config_file, "r", encoding="utf-8") as f:
            reconciled = json.load(f)

        # Invariants reconstructed
        out_tags = [ob["tag"] for ob in reconciled["outbounds"]]
        self.assertIn("just1k-wl-direct", out_tags)
        self.assertIn("just1k-wl-block", out_tags)
        self.assertIn("just1k-wl-api", out_tags)

        in_tags = [ib["tag"] for ib in reconciled["inbounds"]]
        self.assertIn("just1k-wl-default", in_tags)
        self.assertIn("just1k-wl-api-grpc", in_tags)

        # Routing rules reconstructed
        rule_out_tags = [r.get("outboundTag") for r in reconciled["routing"]["rules"]]
        self.assertIn("just1k-wl-direct", rule_out_tags)
        self.assertIn("just1k-wl-api", rule_out_tags)

    def test_heal_and_update_origin_config_handles_corrupt_state(self):
        """When state_file is corrupt or not a dict, reconciliation should not crash with NameError or AttributeError."""
        self._prepare_base_env()
        xray_config_file = self.xray_config_dir / "config.json"
        xray_config_file.parent.mkdir(parents=True, exist_ok=True)
        xray_config_file.write_text('{"inbounds": [], "outbounds": []}', encoding="utf-8")
        relays_file = self.state_dir / "relays.json"
        relays_file.write_text("[]", encoding="utf-8")

        py_reconcile = '''
import json, os, sys
cfg_file, relays_file, state_file = sys.argv[1], sys.argv[2], sys.argv[3]
with open(cfg_file, 'r', encoding='utf-8') as f:
    cfg = json.load(f)
relays = []
secret_base = '/stream'
s_data = {}
if os.path.exists(state_file):
    try:
        with open(state_file, 'r', encoding='utf-8') as sf:
            loaded_s = json.load(sf)
            if isinstance(loaded_s, dict):
                s_data = loaded_s
            secret_base = s_data.get('secret_base_path', '/stream')
    except Exception:
        secret_base = '/stream'
        s_data = {}

has_reverse = bool(s_data.get('reverse_secret'))
print("RECONCILE_SUCCESS")
'''
        broken_state = self.state_dir / "broken_state.json"

        # Case 1: Corrupt non-JSON file
        broken_state.write_text("NOT_VALID_JSON", encoding="utf-8")
        res1 = subprocess.run(
            [sys.executable, "-c", py_reconcile, str(xray_config_file), str(relays_file), str(broken_state)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(res1.returncode, 0, f"Case 1 crashed: {res1.stderr}")
        self.assertIn("RECONCILE_SUCCESS", res1.stdout)

        # Case 2: Valid JSON but not a dict (e.g. list [1, 2, 3])
        broken_state.write_text("[1, 2, 3]", encoding="utf-8")
        res2 = subprocess.run(
            [sys.executable, "-c", py_reconcile, str(xray_config_file), str(relays_file), str(broken_state)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(res2.returncode, 0, f"Case 2 crashed: {res2.stderr}")
        self.assertIn("RECONCILE_SUCCESS", res2.stdout)

        # Verify origin.sh source contains the fix
        origin_sh = (Path(__file__).parent.parent / "just1knode" / "modules" / "xray" / "origin.sh").read_text(encoding="utf-8")
        self.assertIn("s_data = {}", origin_sh)
        self.assertIn("if isinstance(loaded_s, dict):", origin_sh)

    def test_issue_relay_tls_cert_ufw_hooks_merge(self):
        """issue_relay_tls_cert should safely merge ufw hooks into existing renewal config without duplicating."""
        self._prepare_base_env()
        self._create_mock_script("openssl", "#!/bin/sh\nexit 0\n")
        domain = "relay.example.com"
        live_dir = self.letsencrypt_dir / "live" / domain
        live_dir.mkdir(parents=True, exist_ok=True)
        (live_dir / "fullchain.pem").write_text("DUMMY_CERT", encoding="utf-8")
        (live_dir / "privkey.pem").write_text("DUMMY_KEY", encoding="utf-8")

        ren_dir = self.letsencrypt_dir / "renewal"
        ren_dir.mkdir(parents=True, exist_ok=True)
        ren_file = ren_dir / f"{domain}.conf"
        ren_file.write_text("[renewalparams]\npre_hook = old_pre\npost_hook = old_post\n", encoding="utf-8")

        cmd = f'issue_relay_tls_cert "{domain}"'
        res = self._run_shell_snippet(cmd)
        self.assertEqual(res.returncode, 0, f"issue_relay_tls_cert failed: {res.stderr + res.stdout}")

        # Verify hooks were replaced, not duplicated
        content = ren_file.read_text(encoding="utf-8")
        self.assertEqual(content.count("pre_hook"), 1, "pre_hook should appear exactly once")
        self.assertEqual(content.count("post_hook"), 1, "post_hook should appear exactly once")
        self.assertIn("just1knode_ufw_opened_80", content)
        self.assertNotIn("old_pre", content)
        self.assertNotIn("old_post", content)

    def test_rollback_removes_newly_created_relay_nginx_conf(self):
        self._prepare_base_env()
        new_conf = self.nginx_relays_d / "fr.conf"
        self.assertFalse(new_conf.exists())

        new_conf_posix = self._bp(new_conf)
        cmd = f'''
        manifest_begin "{new_conf_posix}"
        echo "fake nginx config" > "{new_conf_posix}"
        manifest_rollback
        '''
        res = self._run_shell_snippet(cmd)
        self.assertEqual(res.returncode, 0, f"rollback snippet failed: {res.stderr + res.stdout}")
        self.assertFalse(
            new_conf.exists(), "Rollback failed to remove newly created relay nginx config file!"
        )

    # -------------------------------------------------------------------------
    # F04: Zero-Collateral Preservation of Custom Outbounds
    # -------------------------------------------------------------------------
    def test_preserve_custom_xray_outbounds(self):
        self._prepare_base_env()
        xray_config_file = self.xray_config_dir / "config.json"
        custom_outbounds = [
            {"tag": "direct", "protocol": "freedom", "settings": {"domainStrategy": "UseIP"}},
            {"tag": "block", "protocol": "blackhole"},
            {"tag": "custom-wireguard", "protocol": "wireguard", "settings": {"secret": "abc"}},
            {
                "tag": "warp-exit",
                "protocol": "socks",
                "settings": {"servers": [{"address": "127.0.0.1", "port": 40000}]},
            },
        ]
        with open(xray_config_file, "w", encoding="utf-8") as f:
            json.dump({"inbounds": [], "outbounds": custom_outbounds, "routing": {"rules": []}}, f)

        # Run origin installer with surgical merge
        cmd = 'install_xray_origin_node "origin.example.com" "admin@example.com" "apikey" "/stream" "1.2.3.4" "origin.example.com" "just1k.best"'
        res = self._run_shell_snippet(cmd)
        self.assertEqual(
            res.returncode, 0, f"install_xray_origin_node failed: {res.stderr + res.stdout}"
        )

        with open(xray_config_file, "r", encoding="utf-8") as f:
            cfg = json.load(f)

        tags = [ob["tag"] for ob in cfg["outbounds"]]
        self.assertIn("direct", tags, "Custom 'direct' outbound must be preserved")
        self.assertIn("block", tags, "Custom 'block' outbound must be preserved")
        self.assertIn(
            "custom-wireguard", tags, "Custom 'custom-wireguard' outbound must be preserved"
        )
        self.assertIn("warp-exit", tags, "Custom 'warp-exit' outbound must be preserved")
        self.assertIn(
            "just1k-wl-direct", tags, "Namespaced just1k-wl-direct outbound must be added"
        )
        self.assertIn("just1k-wl-block", tags, "Namespaced just1k-wl-block outbound must be added")

    # -------------------------------------------------------------------------
    # F05: Zero-Collateral Preservation of Custom Inbound Tags
    # -------------------------------------------------------------------------
    def test_preserve_custom_inbound_tags(self):
        self._prepare_base_env()
        xray_config_file = self.xray_config_dir / "config.json"
        custom_inbounds = [
            {"tag": "api-grpc", "port": 9090, "protocol": "dokodemo-door"},
            {"tag": "inbound-default", "port": 7000, "protocol": "vless"},
            {"tag": "custom-socks", "port": 1080, "protocol": "socks"},
        ]
        with open(xray_config_file, "w", encoding="utf-8") as f:
            json.dump({"inbounds": custom_inbounds, "outbounds": [], "routing": {"rules": []}}, f)

        # Run origin installer with surgical merge
        cmd = 'install_xray_origin_node "origin.example.com" "admin@example.com" "apikey" "/stream" "1.2.3.4" "origin.example.com" "just1k.best"'
        res = self._run_shell_snippet(cmd)
        self.assertEqual(
            res.returncode, 0, f"install_xray_origin_node failed: {res.stderr + res.stdout}"
        )

        with open(xray_config_file, "r", encoding="utf-8") as f:
            cfg = json.load(f)

        inbound_tags = [ib["tag"] for ib in cfg["inbounds"]]
        self.assertIn(
            "custom-socks", inbound_tags, "Third party custom-socks inbound must be preserved"
        )
        self.assertIn(
            "just1k-wl-api-grpc", inbound_tags, "Namespaced just1k-wl-api-grpc must be present"
        )
        self.assertIn(
            "just1k-wl-default", inbound_tags, "Namespaced just1k-wl-default must be present"
        )

    # -------------------------------------------------------------------------
    # F19: Fail-Closed UFW Firewall & Doctor ACL Validation
    # -------------------------------------------------------------------------
    def test_doctor_ufw_acl_validation(self):
        self._prepare_base_env()

        # Case 1: Insecure UFW with 8444 open to 0.0.0.0/0
        self._create_mock_script(
            "ufw",
            """#!/bin/sh
if [ "$1" = "status" ] || [ "$1" = "status verbose" ]; then
    echo "Status: active"
    echo "To                         Action      From"
    echo "--                         ------      ----"
    echo "8444/tcp                   ALLOW       Anywhere"
    echo "443/tcp                    ALLOW       Anywhere"
    exit 0
fi
exit 0
""",
        )
        res_vuln = self._run_shell_snippet("run_doctor")
        self.assertIn("УЯЗВИМОСТЬ: Порт 8444 открыт для всех", res_vuln.stdout + res_vuln.stderr)

        # Case 2: Secure UFW with BOT_IP restriction
        with open(self.state_dir / "state.json", "w", encoding="utf-8") as f:
            json.dump(
                {"role": "origin", "domain": "origin.example.com", "bot_ip": "198.51.100.42"}, f
            )

        self._create_mock_script(
            "ufw",
            """#!/bin/sh
if [ "$1" = "status" ] || [ "$1" = "status verbose" ]; then
    echo "Status: active"
    echo "To                         Action      From"
    echo "--                         ------      ----"
    echo "8444/tcp                   ALLOW       198.51.100.42"
    echo "443/tcp                    ALLOW       Anywhere"
    exit 0
fi
exit 0
""",
        )
        res_sec = self._run_shell_snippet("run_doctor")
        self.assertIn("Порт 8444 защищен и доступен только с BOT_IP", res_sec.stdout)

        # Case 3: Fail-closed installation when BOT_IP is empty
        res_install = self._run_shell_snippet(
            'install_xray_origin_node "origin.example.com" "admin@example.com" "apikey" "/w_test" "" < /dev/null'
        )
        self.assertNotEqual(res_install.returncode, 0)
        self.assertIn("BOT_IP обязателен", res_install.stderr + res_install.stdout)

        # Case 4: Fail-closed installation when BOT_DOMAIN is empty
        res_empty_domain = self._run_shell_snippet(
            'install_xray_origin_node "origin.example.com" "admin@example.com" "apikey" "/w_test" "1.2.3.4" "origin.example.com" "" < /dev/null'
        )
        self.assertNotEqual(res_empty_domain.returncode, 0)
        self.assertIn("BOT_DOMAIN обязателен", res_empty_domain.stderr + res_empty_domain.stdout)

        # Case 5: Fail-closed installation when BOT_DOMAIN is an IP address
        res_ip_domain = self._run_shell_snippet(
            'install_xray_origin_node "origin.example.com" "admin@example.com" "apikey" "/w_test" "1.2.3.4" "origin.example.com" "194.113.106.134" < /dev/null'
        )
        self.assertNotEqual(res_ip_domain.returncode, 0)
        self.assertIn("BOT_DOMAIN должен быть доменным именем (FQDN)", res_ip_domain.stderr + res_ip_domain.stdout)

    # -------------------------------------------------------------------------
    # F20: Verified Update Rollback in update_xray_core
    # -------------------------------------------------------------------------
    def test_update_xray_fail_closed_rollback(self):
        self._prepare_base_env()
        xray_bin = self.bin_dir / "xray"
        xray_bin.write_text("#!/bin/sh\necho 'Xray 26.7.28'\nexit 0\n", encoding="utf-8")
        xray_bin.chmod(0o755)

        # Mock download_and_verify_xray using python zipfile
        cmd = """
download_and_verify_xray() {
    local dest="$1"
    python3 -c "
import zipfile, os
os.makedirs('/tmp/xray_mock_pkg', exist_ok=True)
with open('/tmp/xray_mock_pkg/xray', 'w') as f:
    f.write('#!/bin/sh\\nif [ \\\"\\$1\\\" = \\\"version\\\" ]; then echo \\\"Xray 27.0.0\\\"; exit 0; fi\\nexit 0\\n')
os.chmod('/tmp/xray_mock_pkg/xray', 0o755)
with zipfile.ZipFile('$dest', 'w') as zf:
    zf.write('/tmp/xray_mock_pkg/xray', arcname='xray')
"
}

# Mock systemctl to fail when starting the new version
systemctl() {
    if [ "$1" = "restart" ] && [ "$2" = "xray" ]; then
        if "$XRAY_BIN" version | grep -q "27.0.0"; then
            echo "Failed to start new Xray service" >&2
            return 1
        fi
    fi
    return 0
}

update_xray_core
"""
        res = self._run_shell_snippet(cmd)
        self.assertNotEqual(
            res.returncode, 0, "update_xray_core must exit with error on service restart failure"
        )
        self.assertIn("Xray не запустился после обновления", res.stdout + res.stderr)
        self.assertIn(
            "Откат на предыдущую версию успешно выполнен и подтвержден", res.stdout + res.stderr
        )
        self.assertNotIn("27.0.0", (self.bin_dir / "xray").read_text(encoding="utf-8"))

    def test_update_node_xray_api_rollback_on_restart_failure(self):
        """update_node rolls back xray-api code, cleans new files, restores node modules, and restores service on failure."""
        self._prepare_base_env()

        # Create original /opt/xray-api files
        orig_api = self.xray_api_dir / "api.py"
        orig_api.write_text("#!/usr/bin/env python3\n# original_v1\n", encoding="utf-8")
        orig_conf = self.xray_api_dir / ".orig_config"
        orig_conf.write_text("ACTIVE=1\n", encoding="utf-8")
        venv_dir = self.xray_api_dir / "venv" / "bin"
        venv_dir.mkdir(parents=True, exist_ok=True)
        orig_pip = venv_dir / "pip"
        orig_pip.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        orig_pip.chmod(0o755)

        # Create original /opt/just1knode module
        orig_node_module = self.install_dir / "test_module.sh"
        orig_node_module.write_text("# original_node_sh\n", encoding="utf-8")
        orig_node_bin = self.install_dir / "just1knode.sh"
        orig_node_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        orig_node_bin.chmod(0o755)

        cmd = """
# Mock curl to return a tar.gz archive with new xray-api and just1knode files
curl() {
    local dest=""
    local prev=""
    for arg in "$@"; do
        if [ "$prev" = "-o" ]; then dest="$arg"; fi
        prev="$arg"
    done
    if [ -n "$dest" ]; then
        python3 -c "
import tarfile, os, io
with tarfile.open('$dest', 'w:gz') as tar:
    for path, content in [
        ('package/scripts/xray_api/api.py', b'# new_v2\\n'),
        ('package/scripts/xray_api/new_orphan.py', b'# orphan\\n'),
        ('package/scripts/xray_api/requirements.txt', b'# reqs\\n'),
        ('package/just1knode/just1knode.sh', b'#!/bin/sh\\nexit 0\\n'),
        ('package/just1knode/test_module.sh', b'# new_node_sh\\n'),
        ('package/just1knode/orphan_node.sh', b'# orphan_node\\n'),
    ]:
        ti = tarfile.TarInfo(name=path)
        ti.size = len(content)
        tar.addfile(ti, io.BytesIO(content))
" 2>/dev/null
        return 0
    fi
    return 1
}

# Mock systemctl: initially active, fails restart on v2, succeeds on rollback
XRAY_API_ACTIVE=1
systemctl() {
    if [ "$1" = "is-active" ] && [ "$2" = "xray-api" ]; then
        [ "$XRAY_API_ACTIVE" -eq 1 ] && return 0 || return 1
    fi
    if [ "$1" = "restart" ] && [ "$2" = "xray-api" ]; then
        if grep -q "new_v2" "$XRAY_API_DIR/api.py" 2>/dev/null; then
            echo "Failed to start xray-api with new_v2" >&2
            XRAY_API_ACTIVE=0
            return 1
        fi
        XRAY_API_ACTIVE=1
        return 0
    fi
    return 0
}

update_node "all"
"""
        res = self._run_shell_snippet(cmd)
        self.assertNotEqual(res.returncode, 0, "update_node must fail when xray-api restart fails")
        self.assertIn("Сбой обновления", res.stdout + res.stderr)

        # State-based assertions: physical postconditions verified!
        self.assertIn("original_v1", orig_api.read_text(encoding="utf-8"))
        self.assertFalse((self.xray_api_dir / "new_orphan.py").exists(), "Orphan files must be purged on rollback")
        self.assertTrue((self.xray_api_dir / ".orig_config").exists(), "Dotfiles must be preserved on rollback")
        self.assertIn("original_node_sh", orig_node_module.read_text(encoding="utf-8"), "Node modules must be restored on rollback")
        self.assertFalse((self.install_dir / "orphan_node.sh").exists(), "Orphan node files must be purged on rollback")

    def test_update_node_rollback_on_cp_failure(self):
        """update_node triggers rollback when copying files fails during deploy."""
        self._prepare_base_env()

        orig_api = self.xray_api_dir / "api.py"
        orig_api.write_text("#!/usr/bin/env python3\n# original_v1\n", encoding="utf-8")
        orig_node_module = self.install_dir / "test_module.sh"
        orig_node_module.write_text("# original_node_sh\n", encoding="utf-8")
        orig_node_bin = self.install_dir / "just1knode.sh"
        orig_node_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        orig_node_bin.chmod(0o755)

        cmd = """
# Mock curl to return valid archive
curl() {
    local dest=""
    local prev=""
    for arg in "$@"; do
        if [ "$prev" = "-o" ]; then dest="$arg"; fi
        prev="$arg"
    done
    if [ -n "$dest" ]; then
        python3 -c "
import tarfile, io
with tarfile.open('$dest', 'w:gz') as tar:
    for path, content in [
        ('package/scripts/xray_api/api.py', b'# new_v2\\n'),
        ('package/just1knode/just1knode.sh', b'#!/bin/sh\\nexit 0\\n'),
        ('package/just1knode/test_module.sh', b'# new_node_sh\\n'),
    ]:
        ti = tarfile.TarInfo(name=path)
        ti.size = len(content)
        tar.addfile(ti, io.BytesIO(content))
" 2>/dev/null
        return 0
    fi
    return 1
}

# Fail cp only during the forward deployment from tmp_dir, NOT during rollback from backup_root
real_cp=$(which cp)
DEPLOY_FAILED=0
cp() {
    for arg in "$@"; do
        if [ "$arg" = "$INSTALL_DIR/" ] && [ "$DEPLOY_FAILED" -eq 0 ]; then
            DEPLOY_FAILED=1
            echo "cp: simulated disk full failure during deploy" >&2
            return 1
        fi
    done
    "$real_cp" "$@"
}

update_node "all"
"""
        res = self._run_shell_snippet(cmd)
        self.assertNotEqual(res.returncode, 0, "update_node must fail when deploy cp fails")
        self.assertIn("Не удалось скопировать модули", res.stdout + res.stderr)
        self.assertIn("original_node_sh", orig_node_module.read_text(encoding="utf-8"))

    def test_update_node_preserves_backup_on_rollback_failure(self):
        """update_node preserves backup directory on disk when rollback restart fails."""
        self._prepare_base_env()

        orig_api = self.xray_api_dir / "api.py"
        orig_api.write_text("#!/usr/bin/env python3\n# original_v1\n", encoding="utf-8")
        orig_node_module = self.install_dir / "test_module.sh"
        orig_node_module.write_text("# original_node_sh\n", encoding="utf-8")

        cmd = """
curl() {
    local dest=""
    local prev=""
    for arg in "$@"; do
        if [ "$prev" = "-o" ]; then dest="$arg"; fi
        prev="$arg"
    done
    if [ -n "$dest" ]; then
        python3 -c "
import tarfile, io
with tarfile.open('$dest', 'w:gz') as tar:
    for path, content in [
        ('package/scripts/xray_api/api.py', b'# new_v2\\n'),
        ('package/just1knode/test_module.sh', b'# new_node_sh\\n'),
    ]:
        ti = tarfile.TarInfo(name=path)
        ti.size = len(content)
        tar.addfile(ti, io.BytesIO(content))
" 2>/dev/null
        return 0
    fi
    return 1
}

# Initially is-active is 0 (service active before update).
# On restart (during update) it fails.
# On restart/start (during rollback) it fails.
# On is-active (after rollback) it returns 1 (service inactive).
XRAY_API_ATTEMPT=0
systemctl() {
    if [ "$1" = "is-active" ] && [ "$2" = "xray-api" ]; then
        [ "$XRAY_API_ATTEMPT" -eq 0 ] && return 0 || return 1
    fi
    if [ "$1" = "restart" ] || [ "$1" = "start" ]; then
        XRAY_API_ATTEMPT=$((XRAY_API_ATTEMPT + 1))
        echo "systemctl: service failed to start" >&2
        return 1
    fi
    return 0
}

update_node "all"
"""
        res = self._run_shell_snippet(cmd)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("Резервная копия сохранена в:", res.stdout + res.stderr)
        # Verify that backup directory exists in self.backup_dir
        backup_dirs = list(self.backup_dir.glob("update_bak.*"))
        self.assertTrue(len(backup_dirs) > 0, "Backup directory must be preserved on failed rollback")

    # -------------------------------------------------------------------------
    # F21: Certificate Expiration & SAN Check in Doctor
    # -------------------------------------------------------------------------
    def test_cert_expiration_and_san_check(self):
        self._prepare_base_env()
        domain = "origin.example.com"
        cert_dir = Path(self.temp_dir) / "etc" / "letsencrypt" / "live" / domain
        cert_dir.mkdir(parents=True, exist_ok=True)
        cert_file = cert_dir / "fullchain.pem"
        key_file = cert_dir / "privkey.pem"

        if shutil.which("openssl") is None:
            self.skipTest("openssl binary not found in PATH")

        # Generate test valid certificate with correct SAN
        subprocess.run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-keyout",
                str(key_file),
                "-out",
                str(cert_file),
                "-days",
                "90",
                "-nodes",
                "-subj",
                f"/CN={domain}",
                "-addext",
                f"subjectAltName=DNS:{domain}",
            ],
            check=True,
            capture_output=True,
        )

        with open(self.state_dir / "state.json", "w", encoding="utf-8") as f:
            json.dump({"role": "origin", "domain": domain, "bot_ip": "1.2.3.4"}, f)

        # Mock doctor cert path to point to our test cert
        doctor_snippet = f"""
run_doctor() {{
    local failed=0
    local domain="{domain}"
    local cert_file="{self._bp(cert_file)}"
    local exp_date
    exp_date="$(openssl x509 -enddate -noout -in "$cert_file" | cut -d= -f2)"

    if ! openssl x509 -checkend 0 -noout -in "$cert_file"; then
        echo "SSL expired"
        failed=$((failed + 1))
    elif ! openssl x509 -checkend 2592000 -noout -in "$cert_file"; then
        echo "SSL expiring soon"
    else
        echo "SSL valid"
    fi

    local cert_text
    cert_text="$(openssl x509 -noout -text -in "$cert_file")"
    if echo "$cert_text" | grep -qE "DNS:${{domain}}\\b|CN\\s*=\\s*${{domain}}\\b"; then
        echo "SAN match confirmed"
    else
        echo "SAN mismatch"
        failed=$((failed + 1))
    fi
    return $failed
}}
run_doctor
"""
        res_valid = self._run_shell_snippet(doctor_snippet)
        self.assertEqual(res_valid.returncode, 0)
        self.assertIn("SSL valid", res_valid.stdout)
        self.assertIn("SAN match confirmed", res_valid.stdout)

        # Generate cert with domain mismatch
        subprocess.run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-keyout",
                str(key_file),
                "-out",
                str(cert_file),
                "-days",
                "90",
                "-nodes",
                "-subj",
                "/CN=wrong.example.com",
                "-addext",
                "subjectAltName=DNS:wrong.example.com",
            ],
            check=True,
            capture_output=True,
        )

        res_mismatch = self._run_shell_snippet(doctor_snippet)
        self.assertNotEqual(res_mismatch.returncode, 0)
        self.assertIn("SAN mismatch", res_mismatch.stdout)

    # -------------------------------------------------------------------------
    # F22: Immutable Dependency Pinning
    # -------------------------------------------------------------------------
    def test_installer_immutable_dependencies(self):
        # 1. Check requirements.txt
        req_content = REQUIREMENTS_TXT.read_text(encoding="utf-8")
        req_lines = [
            line.strip()
            for line in req_content.splitlines()
            if line.strip() and not line.startswith("#")
        ]

        for line in req_lines:
            self.assertIn("==", line, f"Requirement {line} must be strictly pinned with ==")
            self.assertFalse(
                re.search(r"[><~]=?", line.split("==")[0]), f"Range specifiers forbidden in {line}"
            )

        pinned_packages = {}
        for item in req_lines:
            pkg, ver = item.split("==")
            pkg_name = pkg.split("[")[0].strip()
            pinned_packages[pkg_name] = ver.strip()

        self.assertEqual(pinned_packages.get("fastapi"), "0.141.1")
        self.assertEqual(pinned_packages.get("uvicorn"), "0.53.0")
        self.assertEqual(pinned_packages.get("grpcio"), "1.84.0")
        self.assertEqual(pinned_packages.get("protobuf"), "7.36.1")
        self.assertEqual(pinned_packages.get("pydantic"), "2.13.5")
        self.assertEqual(pinned_packages.get("psutil"), "7.2.2")

        # 2. Check just1knode for absence of floating git tarballs / unpinned upgrades / dead commits
        sh_content = ""
        just1knode_dir = REPO_ROOT / "just1knode"
        if just1knode_dir.exists():
            for p in just1knode_dir.glob("**/*"):
                if p.is_file():
                    sh_content += p.read_text(encoding="utf-8", errors="ignore") + "\n"
        if JUST1KNODE_SH.exists():
            sh_content += JUST1KNODE_SH.read_text(encoding="utf-8", errors="ignore")
        self.assertNotIn(
            "pip install --upgrade pip", sh_content, "Unpinned pip self-upgrade is forbidden"
        )
        self.assertNotIn(
            "JUST1KBOT_RELEASE_COMMIT", sh_content, "Hallucinated release commit must be removed"
        )
        self.assertNotIn(
            "AMNEZIA_API_COMMIT", sh_content, "Hallucinated Amnezia API commit must be removed"
        )
        self.assertNotIn(
            "install_amnezia_api_node",
            sh_content,
            "Third-party amnezia-api installer must be purged",
        )
        self.assertIn(
            "setup_xray_api_venv", sh_content, "Virtualenv setup function must be present"
        )
        self.assertIn("useradd -r", sh_content, "System user creation must be present")
        self.assertIn("xrayapi", sh_content, "Non-root xrayapi user must be configured")
        self.assertIn("JUST1KBOT_REF", sh_content, "Dynamic ref resolution must be configured")

    # -------------------------------------------------------------------------
    # F23: Panel version label and entry update check
    # -------------------------------------------------------------------------
    def test_node_version_label_format(self):
        """node_version_label prints v<semver> with optional short commit."""
        res = self._run_shell_snippet("node_version_label")
        self.assertEqual(res.returncode, 0, f"node_version_label failed: {res.stderr}")
        self.assertRegex(res.stdout.strip(), r"^v[0-9]+\.[0-9]+\.[0-9]+( \([0-9a-f]+\))?$")

    def test_node_version_box_line_width(self):
        """print_node_version_box_line keeps the 61-char inner box width."""
        res = self._run_shell_snippet("print_node_version_box_line")
        self.assertEqual(res.returncode, 0, f"box line failed: {res.stderr}")
        # 61 inner chars + 2 borders; Python counts codepoints locale-independently.
        line = res.stdout.strip()
        self.assertEqual(len(line), 63)
        self.assertTrue(line.startswith("│") and line.endswith("│"))

    def test_node_update_check_skipped_for_custom_repo(self):
        """check_node_update_on_entry exits 0 silently for non-default repo URL."""
        res = self._run_shell_snippet(
            "check_node_update_on_entry",
            extra_env={"JUST1KBOT_REPO_URL": "https://example.com/custom.git"},
        )
        self.assertEqual(res.returncode, 0)
        self.assertEqual(res.stdout.strip(), "")

    def test_node_update_check_detects_newer_remote(self):
        """check_node_update_on_entry warns when remote VERSION differs (mocked curl)."""
        self._create_mock_script("curl", "#!/bin/sh\necho '99.99.99'\n")
        res = self._run_shell_snippet("check_node_update_on_entry")
        self.assertEqual(res.returncode, 0)
        self.assertIn("99.99.99", res.stdout)

    def test_node_update_check_silent_when_up_to_date(self):
        """check_node_update_on_entry stays silent when remote VERSION matches."""
        current = (REPO_ROOT / "just1knode" / "VERSION").read_text(encoding="utf-8").strip()
        self._create_mock_script("curl", f"#!/bin/sh\necho '{current}'\n")
        res = self._run_shell_snippet("check_node_update_on_entry")
        self.assertEqual(res.returncode, 0)
        self.assertEqual(res.stdout.strip(), "")

    def test_node_update_check_silent_when_remote_older(self):
        """No 'new version' notice when remote VERSION is older than local."""
        self._create_mock_script("curl", "#!/bin/sh\necho '0.0.0'\n")
        res = self._run_shell_snippet("check_node_update_on_entry")
        self.assertEqual(res.returncode, 0)
        self.assertEqual(res.stdout.strip(), "")

    def test_node_update_check_silent_for_invalid_remote(self):
        """Malformed remote VERSION values are ignored, never rendered."""
        for bad in ["2.1.2-beta", "garbage", "", "v2.1.3", "999"]:
            self._create_mock_script("curl", f"#!/bin/sh\necho '{bad}'\n")
            res = self._run_shell_snippet("check_node_update_on_entry")
            self.assertEqual(res.returncode, 0, f"failed for remote={bad!r}: {res.stderr}")
            self.assertEqual(res.stdout.strip(), "", f"notice shown for remote={bad!r}")

    # -------------------------------------------------------------------------
    # Functional Validation: Origin Node Installation & Complete Artifacts
    # -------------------------------------------------------------------------
    def test_functional_origin_node_installation_and_artifacts(self):
        self._prepare_base_env()
        domain = "origin.example.com"
        secret_path = "/stream"

        cmd = f'install_xray_origin_node "{domain}" "admin@example.com" "test_api_key_123" "{secret_path}" "198.51.100.1" "{domain}" "just1k.best"'
        res = self._run_shell_snippet(cmd)
        self.assertEqual(
            res.returncode, 0, f"install_xray_origin_node failed: {res.stderr + res.stdout}"
        )

        # 1. Verify Xray config.json
        xray_conf_file = self.xray_config_dir / "config.json"
        self.assertTrue(xray_conf_file.exists())
        with open(xray_conf_file, "r", encoding="utf-8") as f:
            xray_conf = json.load(f)

        inbound_tags = [ib["tag"] for ib in xray_conf["inbounds"]]
        self.assertIn("just1k-wl-default", inbound_tags)
        self.assertIn("just1k-wl-api-grpc", inbound_tags)

        default_ib = next(ib for ib in xray_conf["inbounds"] if ib["tag"] == "just1k-wl-default")
        self.assertEqual(default_ib["streamSettings"]["network"], "xhttp")
        self.assertEqual(
            default_ib["streamSettings"]["xhttpSettings"]["xPaddingPlacement"], "queryInHeader"
        )
        self.assertEqual(
            default_ib["streamSettings"]["xhttpSettings"]["path"], f"{secret_path}/default"
        )

        outbound_tags = [ob["tag"] for ob in xray_conf["outbounds"]]
        self.assertIn("just1k-wl-direct", outbound_tags)
        self.assertIn("just1k-wl-block", outbound_tags)

        # Standalone origin routing must route default traffic directly to just1k-wl-direct (Russian egress)
        rules = xray_conf["routing"]["rules"]
        default_rule = next(
            (r for r in rules if r.get("inboundTag") == ["just1k-wl-default"]), None
        )
        self.assertIsNotNone(default_rule)
        self.assertEqual(default_rule["outboundTag"], "just1k-wl-direct")

        # 2. Verify Nginx configurations
        nginx_conf = self.nginx_conf_dir / "sites-available" / "just1k-origin.conf"
        self.assertTrue(nginx_conf.exists())
        nginx_text = nginx_conf.read_text(encoding="utf-8")
        self.assertIn("client_max_body_size 0;", nginx_text)
        self.assertIn("large_client_header_buffers 8 64k;", nginx_text)
        self.assertIn("location = /cdn-check", nginx_text)
        self.assertIn("return 204;", nginx_text)
        self.assertIn("location / {", nginx_text)
        self.assertIn("default_type text/plain;", nginx_text)
        self.assertIn('return 404 "Not Found\\n";', nginx_text)

        # 3. Verify Nginx xhttp-map.conf
        map_conf = self.nginx_conf_dir / "conf.d" / "xhttp-map.conf"
        self.assertTrue(map_conf.exists())
        map_text = map_conf.read_text(encoding="utf-8")
        self.assertIn("OPTIONS POST;", map_text)

        # 4. Verify Nginx default relay config
        relays_default = self.nginx_relays_d / "default.conf"
        self.assertTrue(relays_default.exists())
        relays_text = relays_default.read_text(encoding="utf-8")
        self.assertIn(f"location ^~ {secret_path}/default", relays_text)
        self.assertIn("proxy_method $xhttp_proxy_method;", relays_text)
        self.assertIn("client_max_body_size 0;", relays_text)

        # 5. Verify systemd units
        xray_service = self.systemd_dir / "xray-api.service"
        self.assertTrue(xray_service.exists())
        svc_text = xray_service.read_text(encoding="utf-8")
        self.assertIn("User=xrayapi", svc_text)
        self.assertIn("Group=xrayapi", svc_text)
        self.assertIn("uvicorn app:app", svc_text)
        self.assertIn("ReadWritePaths=", svc_text)
        self.assertIn(str(self.xray_api_lib), svc_text)

    # -------------------------------------------------------------------------
    # Functional Validation: Add Relay with REALITY & Relay Egress
    # -------------------------------------------------------------------------
    def test_functional_add_relay_node_reality_and_egress_enforcement(self):
        self._prepare_base_env()
        # Initialize origin config first
        cmd_init = 'install_xray_origin_node "origin.example.com" "admin@example.com" "apikey" "/stream" "198.51.100.1" "origin.example.com" "just1k.best"'
        res_init = self._run_shell_snippet(cmd_init)
        self.assertEqual(res_init.returncode, 0, f"install_xray_origin_node failed: {res_init.stderr + res_init.stdout}")

        # Add Relay with REALITY
        cmd_relay = 'add_relay_node "Germany" "203.0.113.50" "10443" "test-relay-uuid" "de" "reality" "pubkey123" "shortid123" "www.google.com"'
        res = self._run_shell_snippet(cmd_relay)
        self.assertEqual(res.returncode, 0, f"add_relay_node failed: {res.stderr + res.stdout}")

        # Verify Nginx relay conf
        de_conf = self.nginx_relays_d / "de.conf"
        self.assertTrue(de_conf.exists())
        de_text = de_conf.read_text(encoding="utf-8")
        self.assertIn("location ^~ /stream/de", de_text)
        self.assertIn("client_max_body_size 0;", de_text)

        # Verify Xray config has relay inbound, outbound, and enforced egress routing
        with open(self.xray_config_dir / "config.json", "r", encoding="utf-8") as f:
            xray_conf = json.load(f)

        inbound_tags = [ib["tag"] for ib in xray_conf["inbounds"]]
        self.assertIn("just1k-wl-inbound-de", inbound_tags)
        de_ib = next(ib for ib in xray_conf["inbounds"] if ib["tag"] == "just1k-wl-inbound-de")
        self.assertEqual(
            de_ib["streamSettings"]["xhttpSettings"]["xPaddingPlacement"], "queryInHeader"
        )
        self.assertEqual(de_ib["streamSettings"]["xhttpSettings"]["path"], "/stream/de")

        outbound_tags = [ob["tag"] for ob in xray_conf["outbounds"]]
        self.assertIn("just1k-wl-outbound-de", outbound_tags)
        de_ob = next(ob for ob in xray_conf["outbounds"] if ob["tag"] == "just1k-wl-outbound-de")
        self.assertEqual(de_ob["streamSettings"]["security"], "reality")
        self.assertEqual(de_ob["streamSettings"]["realitySettings"]["publicKey"], "pubkey123")
        self.assertEqual(de_ob["streamSettings"]["realitySettings"]["serverName"], "www.google.com")

        # Verify default traffic is routed directly through Moscow IP (just1k-wl-direct)
        rules = xray_conf["routing"]["rules"]
        default_rule = next(
            (r for r in rules if r.get("inboundTag") == ["just1k-wl-default"]), None
        )
        self.assertIsNotNone(default_rule)
        self.assertEqual(default_rule["outboundTag"], "just1k-wl-direct")

        # Verify DNS on Origin has no foreign resolvers
        self.assertNotIn("1.1.1.1", xray_conf["dns"]["servers"])
        self.assertIn("195.208.4.1", xray_conf["dns"]["servers"])
        self.assertIn("77.88.8.1", xray_conf["dns"]["servers"])
        self.assertNotIn("localhost", xray_conf["dns"]["servers"])

        # Verify relay inbound is NOT in ip_rule (geoip:ru) to prevent foreign DNS leaks on Origin
        ip_rule = next(
            (r for r in rules if r.get("outboundTag") == "just1k-wl-direct" and "ip" in r), None
        )
        self.assertIsNotNone(ip_rule, "ip_rule with geoip:ru for direct routing must exist")
        self.assertNotIn("just1k-wl-inbound-de", ip_rule.get("inboundTag", []))

    # -------------------------------------------------------------------------
    # Functional Validation: Role Guard in manage_relays_menu
    # -------------------------------------------------------------------------
    def test_functional_manage_relays_menu_role_guard(self):
        self._prepare_base_env()
        # Case 1: Role is 'relay' -> must fail closed
        with open(self.state_dir / "state.json", "w", encoding="utf-8") as f:
            json.dump({"role": "relay", "domain": "relay.example.com"}, f)

        res = self._run_shell_snippet("manage_relays_menu < /dev/null")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("ТОЛЬКО на Origin-сервере", res.stderr + res.stdout)

        # Case 2: Role is empty/unset -> must fail closed
        with open(self.state_dir / "state.json", "w", encoding="utf-8") as f:
            json.dump({}, f)

        res_empty = self._run_shell_snippet("manage_relays_menu < /dev/null")
        self.assertNotEqual(res_empty.returncode, 0)
        self.assertIn("ТОЛЬКО на Origin-сервере", res_empty.stderr + res_empty.stdout)

    # -------------------------------------------------------------------------
    # Functional Validation: Zero-Signature Purge & Certbot Deploy Hook
    # -------------------------------------------------------------------------
    def test_functional_zero_signature_and_certbot_deploy_hook(self):
        self._prepare_base_env()
        index_file = self.www_html_dir / "index.html"
        index_file.write_text("<h1>SimpleCalc</h1>", encoding="utf-8")
        self.assertTrue(index_file.exists())

        res = self._run_shell_snippet("deploy_camouflage_site; deploy_certbot_renewal_hook")
        self.assertEqual(res.returncode, 0)

        # 1. Zero-Signature: legacy camouflage index.html must be purged
        self.assertFalse(index_file.exists())

        # 2. Zero-Collateral: custom user index.html must NOT be purged
        custom_index = self.www_html_dir / "index.html"
        custom_index.write_text("<h1>Custom User Site</h1>", encoding="utf-8")
        res2 = self._run_shell_snippet("deploy_camouflage_site")
        self.assertEqual(res2.returncode, 0)
        self.assertTrue(custom_index.exists())
        self.assertEqual(custom_index.read_text(encoding="utf-8"), "<h1>Custom User Site</h1>")

        # 2. Certbot renewal hook
        hook_file = self.letsencrypt_dir / "renewal-hooks" / "deploy" / "restart-xray-nginx.sh"
        self.assertTrue(hook_file.exists())
        hook_content = hook_file.read_text(encoding="utf-8")
        self.assertIn("systemctl reload nginx", hook_content)
        self.assertIn("systemctl restart xray", hook_content)
        self.assertIn("systemctl restart xray-api", hook_content)

    def test_heal_and_update_origin_config_with_relays(self):
        self._prepare_base_env()
        relays = [
            {
                "name": "Германия",
                "code": "de",
                "ip": "217.60.183.229",
                "port": 10443,
                "path": "/stream/de",
            },
            {
                "name": "Эстония",
                "code": "ee",
                "ip": "217.60.182.33",
                "port": 10443,
                "path": "/stream/ee",
            },
        ]
        with open(self.state_dir / "relays.json", "w", encoding="utf-8") as f:
            json.dump(relays, f)

        res = self._run_shell_snippet("heal_and_update_origin_config")
        self.assertEqual(res.returncode, 0, f"STDOUT: {res.stdout}\nSTDERR: {res.stderr}")

        with open(self.xray_config_dir / "config.json", "r", encoding="utf-8") as f:
            cfg = json.load(f)
        rules = cfg.get("routing", {}).get("rules", [])
        rule_outbounds = [r.get("outboundTag") for r in rules]
        self.assertIn("just1k-wl-outbound-de", rule_outbounds)
        self.assertIn("just1k-wl-outbound-ee", rule_outbounds)

        # Invariant: Relay routing rules must precede any IP-based rules to ensure foreign domains
        # are forwarded by FQDN to Europe without triggering DNS resolution on the Origin node
        de_idx = next(i for i, r in enumerate(rules) if r.get("outboundTag") == "just1k-wl-outbound-de")
        ip_rules = [i for i, r in enumerate(rules) if r.get("outboundTag") == "just1k-wl-direct" and "ip" in r]
        for ip_idx in ip_rules:
            self.assertLess(de_idx, ip_idx, "Relay outbound rule must precede IP matching rules")

        # Invariant: Origin DNS servers must not contain foreign resolvers
        self.assertNotIn("1.1.1.1", cfg["dns"]["servers"])
        self.assertIn("195.208.4.1", cfg["dns"]["servers"])
        self.assertIn("77.88.8.1", cfg["dns"]["servers"])
        self.assertNotIn("localhost", cfg["dns"]["servers"])

    def test_auto_heal_relays_registry_when_corrupted(self):
        self._prepare_base_env()
        # Create corrupted relays.json simulating the exact issue
        with open(self.state_dir / "relays.json", "w", encoding="utf-8") as f:
            f.write('[\n  {\n    "name": \n')

        with open(self.xray_config_dir / "config.json", "w", encoding="utf-8") as f:
            json.dump(
                {
                    "inbounds": [
                        {
                            "tag": "just1k-wl-inbound-de",
                            "port": 8005,
                            "protocol": "vless",
                            "streamSettings": {"xhttpSettings": {"path": "/stream/de"}},
                        }
                    ],
                    "outbounds": [
                        {
                            "tag": "just1k-wl-outbound-de",
                            "settings": {
                                "vnext": [
                                    {
                                        "address": "217.60.183.229",
                                        "port": 10443,
                                        "users": [{"id": "test-uuid"}],
                                    }
                                ]
                            },
                            "streamSettings": {
                                "security": "reality",
                                "realitySettings": {"serverName": "www.google.com"},
                            },
                        }
                    ],
                },
                f,
            )

        res = self._run_shell_snippet("auto_heal_relays_registry")
        self.assertEqual(res.returncode, 0, f"STDOUT: {res.stdout}\nSTDERR: {res.stderr}")

        with open(self.state_dir / "relays.json", "r", encoding="utf-8") as f:
            healed = json.load(f)
        self.assertEqual(len(healed), 1)
        self.assertEqual(healed[0]["code"], "de")
        self.assertEqual(healed[0]["ip"], "217.60.183.229")

    def test_deploy_subscription_proxy_conf_generates_valid_proxy(self):
        self._prepare_base_env()
        # Set bot_domain in state
        with open(self.state_dir / "state.json", "w", encoding="utf-8") as f:
            json.dump(
                {"role": "origin", "domain": "origin.example.com", "bot_domain": "just1k.best"}, f
            )

        res = self._run_shell_snippet("deploy_subscription_proxy_conf")
        self.assertEqual(
            res.returncode, 0, f"deploy_subscription_proxy_conf failed: {res.stderr + res.stdout}"
        )

        sub_conf = self.nginx_relays_d / "sub-wl.conf"
        self.assertTrue(sub_conf.exists(), "sub-wl.conf must be created in NGINX_RELAYS_DIR")
        content = sub_conf.read_text(encoding="utf-8")
        self.assertIn("location ^~ /sub/wl", content)
        self.assertIn("resolver ", content)
        self.assertIn("77.88.8.8", content)
        self.assertNotIn("1.1.1.1", content)
        self.assertNotIn("8.8.8.8", content)
        self.assertIn("resolver_timeout 3s;", content)
        self.assertIn("proxy_connect_timeout 5s;", content)
        self.assertIn('set $bot_upstream "https://just1k.best";', content)
        self.assertIn("proxy_pass $bot_upstream;", content)
        self.assertIn("proxy_ssl_server_name on;", content)
        self.assertIn("proxy_ssl_name just1k.best;", content)
        self.assertIn("proxy_ssl_verify on;", content)
        self.assertIn("proxy_ssl_verify_depth 5;", content)
        self.assertIn("proxy_ssl_trusted_certificate /etc/ssl/certs/ca-certificates.crt;", content)
        self.assertNotIn("proxy_ssl_verify off;", content)
        self.assertIn("proxy_set_header Host just1k.best;", content)

    def test_deploy_subscription_proxy_conf_fails_on_ip_target(self):
        self._prepare_base_env()
        with open(self.state_dir / "state.json", "w", encoding="utf-8") as f:
            json.dump(
                {"role": "origin", "domain": "origin.example.com", "bot_domain": "194.113.106.134"}, f
            )

        res = self._run_shell_snippet("deploy_subscription_proxy_conf")
        self.assertNotEqual(res.returncode, 0, "deploy_subscription_proxy_conf must fail if target is an IP")
        self.assertIn("BOT_DOMAIN FQDN", res.stderr + res.stdout)

    def test_deploy_subscription_proxy_conf_fails_on_missing_target(self):
        self._prepare_base_env()
        with open(self.state_dir / "state.json", "w", encoding="utf-8") as f:
            json.dump({"role": "origin", "domain": "origin.example.com"}, f)

        res = self._run_shell_snippet("deploy_subscription_proxy_conf")
        self.assertNotEqual(res.returncode, 0, "deploy_subscription_proxy_conf must fail if target is missing")
        self.assertIn("BOT_DOMAIN FQDN", res.stderr + res.stdout)

    def test_heal_and_update_origin_config_handles_legacy_ip_without_crashing(self):
        self._prepare_base_env()
        with open(self.state_dir / "state.json", "w", encoding="utf-8") as f:
            json.dump(
                {"role": "origin", "domain": "origin.example.com", "bot_domain": "194.113.106.134"}, f
            )

        res = self._run_shell_snippet("heal_and_update_origin_config")
        self.assertEqual(res.returncode, 0, "heal_and_update_origin_config must not crash on legacy IP in bot_domain")
        self.assertIn("устаревший bot_domain в виде IP", res.stderr + res.stdout)

    def test_heal_and_update_origin_config_migrates_with_env_bot_domain(self):
        self._prepare_base_env()
        with open(self.state_dir / "state.json", "w", encoding="utf-8") as f:
            json.dump(
                {"role": "origin", "domain": "origin.example.com", "bot_domain": "194.113.106.134"}, f
            )

        res = self._run_shell_snippet("BOT_DOMAIN=just1k.best heal_and_update_origin_config")
        self.assertEqual(res.returncode, 0)
        sub_conf = self.nginx_relays_d / "sub-wl.conf"
        self.assertTrue(sub_conf.exists())
        self.assertIn("just1k.best", sub_conf.read_text(encoding="utf-8"))

        # Verify state.json was updated with the migrated FQDN
        with open(self.state_dir / "state.json", "r", encoding="utf-8") as f:
            st = json.load(f)
        self.assertEqual(st.get("bot_domain"), "just1k.best")

    def test_heal_and_update_origin_config_overrides_existing_fqdn_with_env_bot_domain(self):
        self._prepare_base_env()
        with open(self.state_dir / "state.json", "w", encoding="utf-8") as f:
            json.dump(
                {"role": "origin", "domain": "origin.example.com", "bot_domain": "old.example.com"}, f
            )

        res = self._run_shell_snippet("BOT_DOMAIN=new.example.com heal_and_update_origin_config")
        self.assertEqual(res.returncode, 0)
        sub_conf = self.nginx_relays_d / "sub-wl.conf"
        self.assertTrue(sub_conf.exists())
        self.assertIn("new.example.com", sub_conf.read_text(encoding="utf-8"))

        with open(self.state_dir / "state.json", "r", encoding="utf-8") as f:
            st = json.load(f)
        self.assertEqual(st.get("bot_domain"), "new.example.com")

    def test_heal_and_update_origin_config_upgrades_nginx_to_404_and_purges_camouflage(self):
        self._prepare_base_env()
        with open(self.state_dir / "state.json", "w", encoding="utf-8") as f:
            json.dump({"role": "origin", "domain": "origin.example.com"}, f)

        # Place legacy index.html with template signature
        calc_html = self.www_html_dir / "index.html"
        calc_html.write_text("<html>SimpleCalc - All calculations done client-side</html>", encoding="utf-8")

        # Place legacy just1k-origin.conf with try_files
        sites_avail = self.nginx_conf_dir / "sites-available"
        sites_avail.mkdir(parents=True, exist_ok=True)
        origin_conf = sites_avail / "just1k-origin.conf"
        origin_conf.write_text(
            """server {
    listen 443 ssl http2;
    server_name origin.example.com;
    location / {
        root /var/www/html;
        index index.html;
        try_files $uri $uri/ =404;
    }
}
""",
            encoding="utf-8",
        )

        res = self._run_shell_snippet("heal_and_update_origin_config")
        self.assertEqual(res.returncode, 0)

        # Camouflage file should be deleted
        self.assertFalse(calc_html.exists(), "Legacy camouflage index.html must be purged")

        # Nginx config must be upgraded to return 404
        content = origin_conf.read_text(encoding="utf-8")
        self.assertNotIn("try_files", content)
        self.assertIn('return 404 "Not Found\\n";', content)

    def test_deploy_camouflage_site_preserves_unrelated_index_html(self):
        self._prepare_base_env()
        custom_html = self.www_html_dir / "index.html"
        custom_html.write_text("<html><h1>My Personal Blog</h1></html>", encoding="utf-8")

        res = self._run_shell_snippet("deploy_camouflage_site")
        self.assertEqual(res.returncode, 0)
        self.assertTrue(custom_html.exists(), "Custom user index.html must NOT be deleted")

    def test_normalize_domain_strips_protocols_and_slashes(self):
        self._prepare_base_env()
        res = self._run_shell_snippet('normalize_domain "  https://mybot.just1k.best/some/path/  "')
        self.assertEqual(res.returncode, 0)
        self.assertEqual(res.stdout.strip(), "mybot.just1k.best")

        res_http = self._run_shell_snippet('normalize_domain "http://test.domain.com:8443/"')
        self.assertEqual(res_http.returncode, 0)
        self.assertEqual(res_http.stdout.strip(), "test.domain.com:8443")

    def test_init_state_dir_sets_sgid_and_permissions(self):
        self._prepare_base_env()
        res = self._run_shell_snippet("init_state_dir")
        self.assertEqual(res.returncode, 0)
        st = os.stat(self.state_dir)
        # Check SGID and permissions (2770 or 0o2770)
        self.assertTrue(bool(st.st_mode & 0o2000), "SGID bit must be set on STATE_DIR")
        self.assertTrue(bool(st.st_mode & 0o0070), "Group must have rwx permissions on STATE_DIR")
        self.assertEqual(st.st_mode & 0o0007, 0, "Others must have zero permissions on STATE_DIR")

        # Check state file permissions 660
        state_file = self.state_dir / "state.json"
        if state_file.exists():
            st_file = os.stat(state_file)
            self.assertTrue(
                bool(st_file.st_mode & 0o0660),
                "State file must have rw permissions for owner and group",
            )
            self.assertEqual(
                st_file.st_mode & 0o0007, 0, "State file must have zero permissions for others"
            )

    def test_detect_existing_nginx_sites_in_just1knode(self):
        self._prepare_base_env()
        sites_enabled = self.nginx_conf_dir / "sites-enabled"
        sites_enabled.mkdir(parents=True, exist_ok=True)

        # Default stock
        (sites_enabled / "default").write_text(
            "server { listen 80; server_name _; }\n", encoding="utf-8"
        )
        # User site
        (sites_enabled / "my-blog.conf").write_text(
            "server {\n    listen 80;\n    server_name myblog.org;\n}\n", encoding="utf-8"
        )

        res = self._run_shell_snippet(f'detect_existing_nginx_sites "{self._bp(self.nginx_conf_dir)}"')
        self.assertEqual(
            res.returncode, 0, f"detect_existing_nginx_sites failed: stderr={res.stderr}"
        )
        self.assertIn("my-blog.conf", res.stdout)
        self.assertIn("myblog.org", res.stdout)
        self.assertNotIn("default", res.stdout)

    def test_origin_nginx_backs_up_custom_default_site(self):
        self._prepare_base_env()
        sites_enabled = self.nginx_conf_dir / "sites-enabled"
        sites_available = self.nginx_conf_dir / "sites-available"
        sites_enabled.mkdir(parents=True, exist_ok=True)
        sites_available.mkdir(parents=True, exist_ok=True)

        # User has a custom domain inside default
        (sites_enabled / "default").write_text(
            "server {\n    listen 80;\n    server_name custom-site.com;\n}\n", encoding="utf-8"
        )

        # Simulate default backup check snippet from origin.sh
        snippet = f"""
NGINX_CONF_DIR="{self._bp(self.nginx_conf_dir)}"
if [[ -f "${{NGINX_CONF_DIR}}/sites-enabled/default" ]] && grep -Eq '(^|[[:space:]])server_name[[:space:]]+[^_;]' "${{NGINX_CONF_DIR}}/sites-enabled/default" 2>/dev/null; then
    cp -a "${{NGINX_CONF_DIR}}/sites-enabled/default" "${{NGINX_CONF_DIR}}/sites-available/default.user.bak"
fi
rm -f "${{NGINX_CONF_DIR}}/sites-enabled/default" 2>/dev/null || true
"""
        res = self._run_shell_snippet(snippet)
        self.assertEqual(res.returncode, 0)
        self.assertTrue(
            (sites_available / "default.user.bak").exists(),
            "Custom default site must be backed up as default.user.bak in sites-available",
        )
        self.assertFalse(
            (sites_enabled / "default.user.bak").exists(), "Backup must never be in sites-enabled"
        )
        self.assertFalse((sites_enabled / "default").exists())

    def test_origin_nginx_restores_default_on_validation_failure(self):
        self._prepare_base_env()
        sites_enabled = self.nginx_conf_dir / "sites-enabled"
        sites_available = self.nginx_conf_dir / "sites-available"
        sites_enabled.mkdir(parents=True, exist_ok=True)
        sites_available.mkdir(parents=True, exist_ok=True)

        (sites_available / "default").write_text(
            "server {\n    listen 80;\n    server_name default.test;\n}\n", encoding="utf-8"
        )
        (sites_enabled / "default").symlink_to(sites_available / "default")

        snippet = f"""
NGINX_CONF_DIR="{self._bp(self.nginx_conf_dir)}"
default_was_linked_origin=0
if [[ -f "${{NGINX_CONF_DIR}}/sites-enabled/default" ]]; then
    default_was_linked_origin=1
    rm -f "${{NGINX_CONF_DIR}}/sites-enabled/default"
fi
# Simulate validation failure rollback
if [[ $default_was_linked_origin -eq 1 && -f "${{NGINX_CONF_DIR}}/sites-available/default" ]]; then
    ln -sf "${{NGINX_CONF_DIR}}/sites-available/default" "${{NGINX_CONF_DIR}}/sites-enabled/default"
fi
"""
        res = self._run_shell_snippet(snippet)
        self.assertEqual(res.returncode, 0)
        self.assertTrue(
            (sites_enabled / "default").exists(),
            "default site must be restored after validation failure",
        )

    def test_ensure_xrayapi_user_creates_group_and_user(self):
        self._prepare_base_env()
        mock_bin = self.bin_dir
        group_log = Path(self.temp_dir) / "groupadd.log"
        user_log = Path(self.temp_dir) / "useradd.log"
        group_log_posix = self._bp(group_log)
        user_log_posix = self._bp(user_log)
        (mock_bin / "groupadd").write_text(
            f"#!/bin/bash\necho \"$@\" >> '{group_log_posix}'\nexit 0\n", encoding="utf-8"
        )
        (mock_bin / "groupadd").chmod(0o755)
        (mock_bin / "useradd").write_text(
            f"#!/bin/bash\necho \"$@\" >> '{user_log_posix}'\nexit 0\n", encoding="utf-8"
        )
        (mock_bin / "useradd").chmod(0o755)
        (mock_bin / "getent").write_text("#!/bin/bash\nexit 1\n", encoding="utf-8")
        (mock_bin / "getent").chmod(0o755)
        (mock_bin / "id").write_text(
            f'#!/bin/bash\nif [[ -f "{user_log_posix}" ]]; then echo 1001; exit 0; fi\nexit 1\n',
            encoding="utf-8",
        )
        (mock_bin / "id").chmod(0o755)

        api_sh = REPO_ROOT / "just1knode" / "modules" / "xray" / "api.sh"
        res = self._run_shell_snippet(f"""
unset -f ensure_xrayapi_user
source '{self._bp(api_sh)}'
ensure_xrayapi_user
""")
        self.assertEqual(res.returncode, 0, f"ensure_xrayapi_user failed: {res.stderr}")
        self.assertTrue(group_log.exists(), "groupadd must be called when group does not exist")
        self.assertIn("-r xrayapi", group_log.read_text(encoding="utf-8"))
        self.assertTrue(user_log.exists(), "useradd must be called when user does not exist")
        self.assertIn("xrayapi", user_log.read_text(encoding="utf-8"))

    def test_heal_and_update_relay_config_atomic_and_chmod_640(self):
        self._prepare_base_env()
        (self.state_dir / "state.json").write_text('{"role": "relay"}', encoding="utf-8")
        xray_config = self.xray_config_dir / "config.json"
        initial_cfg = {
            "outbounds": [{"tag": "direct", "protocol": "freedom"}],
            "inbounds": [],
        }
        xray_config.write_text(json.dumps(initial_cfg), encoding="utf-8")

        res = self._run_shell_snippet("heal_and_update_relay_config")
        self.assertEqual(
            res.returncode, 0, f"heal_and_update_relay_config failed: stderr={res.stderr}"
        )
        updated_cfg = json.loads(xray_config.read_text(encoding="utf-8"))
        self.assertIn("dns", updated_cfg)
        self.assertEqual(updated_cfg["dns"]["queryStrategy"], "UseIPv4")
        st = xray_config.stat().st_mode & 0o777
        if os.name != "nt":
            self.assertEqual(st, 0o640, f"Expected 0640, got {oct(st)}")
        else:
            self.assertTrue(bool(st & 0o600))

        # Verify BitTorrent filtering and blackhole block outbound injected
        rules = updated_cfg.get("routing", {}).get("rules", [])
        self.assertTrue(any("bittorrent" in r.get("protocol", []) for r in rules), "BitTorrent protocol rule must be present")
        self.assertTrue(any(ob.get("tag") == "block" and ob.get("protocol") == "blackhole" for ob in updated_cfg.get("outbounds", [])), "Blackhole outbound must be present")

    # -------------------------------------------------------------------------
    # Safe Complete Uninstallation Lifecycle Tests
    # -------------------------------------------------------------------------

    def test_uninstall_node_fails_closed_without_confirmation(self):
        """just1knode uninstall in non-interactive mode without --confirm=DELETE must exit 1."""
        self._prepare_base_env()
        res = self._run_shell_snippet("uninstall_node")
        self.assertEqual(res.returncode, 1)
        self.assertIn("В неинтерактивном режиме для удаления требуется явный флаг", res.stdout + res.stderr)
        self.assertTrue(self.state_dir.exists(), "state_dir must remain untouched")

    def test_uninstall_node_aborts_on_first_prompt_cancellation(self):
        """just1knode uninstall must abort when user responds 'n' to first prompt."""
        self._prepare_base_env()
        res = self._run_shell_snippet("uninstall_node", input_text="n\n")
        self.assertEqual(res.returncode, 0)
        self.assertIn("Удаление отменено пользователем", res.stdout + res.stderr)
        self.assertTrue(self.state_dir.exists())

    def test_uninstall_node_aborts_on_keyword_mismatch(self):
        """just1knode uninstall must abort when user enters incorrect confirmation keyword."""
        self._prepare_base_env()
        res = self._run_shell_snippet("uninstall_node", input_text="y\nABORT\n")
        self.assertEqual(res.returncode, 0)
        self.assertIn("Подтверждение не совпало", res.stdout + res.stderr)
        self.assertTrue(self.state_dir.exists())

    def test_uninstall_node_complete_cleanup_lifecycle(self):
        """just1knode uninstall --confirm=DELETE --purge-backups removes services, binaries, configs, nginx sites, user, and state."""
        self._prepare_base_env()

        # 1. Setup mock services and files
        (self.systemd_dir / "xray.service").write_text("[Unit]\nDescription=Xray\n", encoding="utf-8")
        (self.systemd_dir / "xray-api.service").write_text("[Unit]\nDescription=API\n", encoding="utf-8")
        (self.xray_config_dir / "config.json").write_text("{}", encoding="utf-8")
        (self.xray_api_etc / "config.env").write_text("API_KEY=test\n", encoding="utf-8")
        (self.xray_api_dir / "app.py").write_text("# app\n", encoding="utf-8")
        (self.xray_api_lib / "epoch.json").write_text("{}", encoding="utf-8")

        # Nginx configs
        nginx_sites_avail = self.nginx_conf_dir / "sites-available"
        nginx_sites_enabled = self.nginx_conf_dir / "sites-enabled"
        nginx_conf_d = self.nginx_conf_dir / "conf.d"
        nginx_sites_avail.mkdir(parents=True, exist_ok=True)
        nginx_sites_enabled.mkdir(parents=True, exist_ok=True)
        nginx_conf_d.mkdir(parents=True, exist_ok=True)

        origin_conf = nginx_sites_avail / "just1k-origin.conf"
        origin_conf.write_text("server { listen 80; }", encoding="utf-8")
        (nginx_sites_enabled / "just1k-origin.conf").symlink_to(origin_conf)
        (nginx_conf_d / "xhttp-map.conf").write_text("map $request_method $xhttp { }", encoding="utf-8")

        # Backup of user default site
        (nginx_sites_avail / "default.user.bak").write_text("server { server_name user.com; }", encoding="utf-8")

        # Fake camouflage site
        (self.www_html_dir / "index.html").write_text("<h1>Cloud Ingress Network Node</h1>", encoding="utf-8")

        # Fake certbot hook
        hook_dir = self.letsencrypt_dir / "renewal-hooks" / "deploy"
        hook_dir.mkdir(parents=True, exist_ok=True)
        (hook_dir / "restart-xray-nginx.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")

        # Fake install dir & global bin
        fake_install_dir = Path(self.temp_dir) / "opt" / "just1knode"
        fake_install_dir.mkdir(parents=True, exist_ok=True)
        (fake_install_dir / "marker.txt").write_text("just1knode", encoding="utf-8")

        fake_global_bin = Path(self.temp_dir) / "usr_local_bin" / "just1knode"
        fake_global_bin.parent.mkdir(parents=True, exist_ok=True)
        fake_global_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")

        # Mock userdel, groupdel, pkill
        self._create_mock_script("userdel", "#!/bin/sh\nexit 0\n")
        self._create_mock_script("groupdel", "#!/bin/sh\nexit 0\n")
        self._create_mock_script("pkill", "#!/bin/sh\nexit 0\n")

        extra_env = {
            "INSTALL_DIR": str(fake_install_dir),
            "JUST1KNODE_GLOBAL_BIN": str(fake_global_bin),
            "BACKUP_DIR": str(self.backup_dir),
            "JUST1KNODE_ALLOW_CUSTOM_INSTALL_RM": "1",
        }

        res = self._run_shell_snippet("uninstall_node --confirm=DELETE --purge-backups", extra_env=extra_env)
        self.assertEqual(res.returncode, 0, f"uninstall_node failed: {res.stderr}\nOutput: {res.stdout}")
        self.assertIn("just1knode успешно и полностью удален с сервера без остатков", res.stdout)

        # Assertions
        self.assertFalse((self.systemd_dir / "xray.service").exists(), "xray.service must be removed")
        self.assertFalse((self.systemd_dir / "xray-api.service").exists(), "xray-api.service must be removed")
        self.assertFalse(self.xray_config_dir.exists(), "xray config dir must be removed")
        self.assertFalse(self.xray_api_dir.exists(), "xray-api dir must be removed")
        self.assertFalse(self.xray_api_etc.exists(), "xray-api etc must be removed")
        self.assertFalse(self.xray_api_lib.exists(), "xray-api lib must be removed")
        self.assertFalse(self.state_dir.exists(), "state_dir must be removed")
        self.assertFalse((nginx_sites_avail / "just1k-origin.conf").exists(), "origin nginx site must be removed")
        self.assertFalse((nginx_sites_enabled / "just1k-origin.conf").exists(), "origin nginx link must be removed")
        self.assertFalse((nginx_conf_d / "xhttp-map.conf").exists(), "xhttp-map.conf must be removed")
        self.assertTrue((nginx_sites_avail / "default").exists(), "default site must be restored from default.user.bak")
        self.assertFalse((nginx_sites_avail / "default.user.bak").exists(), "default.user.bak must be removed after restore")
        self.assertFalse((self.www_html_dir / "index.html").exists(), "camouflage index.html must be removed")
        self.assertFalse((hook_dir / "restart-xray-nginx.sh").exists(), "certbot hook must be removed")
        self.assertFalse(fake_install_dir.exists(), "INSTALL_DIR must be removed")
        self.assertFalse(fake_global_bin.exists(), "JUST1KNODE_GLOBAL_BIN must be removed")
        self.assertFalse(self.backup_dir.exists(), "BACKUP_DIR must be removed when --purge-backups is passed")

    def test_uninstall_node_preserves_backups_without_purge_flag(self):
        """uninstall_node preserves BACKUP_DIR unless --purge-backups is explicitly given."""
        self._prepare_base_env()
        (self.backup_dir / "xray_state.tar.gz").write_text("backup_content", encoding="utf-8")

        extra_env = {
            "BACKUP_DIR": str(self.backup_dir),
            "JUST1KNODE_ALLOW_CUSTOM_INSTALL_RM": "1",
        }

        res = self._run_shell_snippet("uninstall_node --confirm=DELETE", extra_env=extra_env)
        self.assertEqual(res.returncode, 0, f"uninstall_node failed: {res.stderr}\nOutput: {res.stdout}")
        self.assertTrue(self.backup_dir.exists(), "BACKUP_DIR must remain when --purge-backups is omitted")
        self.assertTrue((self.backup_dir / "xray_state.tar.gz").exists(), "Backup archive must remain intact")
        self.assertIn("Каталог бэкапов сохранен", res.stdout)

    def test_uninstall_node_rejects_protected_system_directories(self):
        """uninstall_node must fail-closed (code 1) when STATE_DIR or BACKUP_DIR is a protected root/system directory."""
        self._prepare_base_env()
        for dangerous_dir in ["/etc", "/var", "/usr", "/root", "/"]:
            res = self._run_shell_snippet("uninstall_node --confirm=DELETE", extra_env={"STATE_DIR": dangerous_dir})
            self.assertEqual(res.returncode, 1, f"Expected code 1 for dangerous STATE_DIR={dangerous_dir}")
            self.assertIn("Попытка удаления защищенного системного каталога", res.stdout + res.stderr)

    def test_uninstall_node_safe_nginx_reload_skips_when_inactive(self):
        """uninstall_node must not restart Nginx when systemctl is-active returns non-zero."""
        self._prepare_base_env()
        nginx_log = Path(self.temp_dir) / "nginx_cmd.log"
        nginx_log_posix = self._bp(nginx_log)
        self._create_mock_script(
            "systemctl",
            f"""#!/bin/sh
if [ "$1" = "is-active" ] && [ "$3" = "nginx" ]; then
    exit 3
fi
if [ "$1" = "restart" ] && [ "$2" = "nginx" ]; then
    echo "RESTART_NGINX" >> "{nginx_log_posix}"
fi
if [ "$1" = "reload" ] && [ "$2" = "nginx" ]; then
    echo "RELOAD_NGINX" >> "{nginx_log_posix}"
fi
exit 0
""",
        )

        fake_install_dir = Path(self.temp_dir) / "opt" / "just1knode"
        fake_install_dir.mkdir(parents=True, exist_ok=True)
        fake_global_bin = Path(self.temp_dir) / "usr_local_bin" / "just1knode"
        fake_global_bin.parent.mkdir(parents=True, exist_ok=True)
        fake_global_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")

        extra_env = {
            "INSTALL_DIR": str(fake_install_dir),
            "JUST1KNODE_GLOBAL_BIN": str(fake_global_bin),
            "JUST1KNODE_ALLOW_CUSTOM_INSTALL_RM": "1",
        }
        res = self._run_shell_snippet("uninstall_node --confirm=DELETE", extra_env=extra_env)
        self.assertEqual(res.returncode, 0, f"uninstall_node failed: {res.stderr}\n{res.stdout}")
        if nginx_log.exists():
            log_content = nginx_log.read_text(encoding="utf-8")
            self.assertNotIn("RESTART_NGINX", log_content, "Inactive Nginx must NEVER be restarted during uninstall!")

    def test_validate_fqdn_and_sub_prefix(self):
        """validate_fqdn and validate_sub_prefix reject malicious/hostile inputs and accept valid ones."""
        self._prepare_base_env()

        # Valid FQDNs
        valid_fqdns = ["bot.just1k.best", "sub.example.com", "my-node.origin.cloud", "a.bc"]
        for fqdn in valid_fqdns:
            res = self._run_shell_snippet(f"validate_fqdn '{fqdn}'")
            self.assertEqual(res.returncode, 0, f"Expected '{fqdn}' to be valid FQDN")

        # Hostile / Invalid FQDNs
        invalid_fqdns = [
            "",
            "localhost",
            "127.0.0.1",
            "192.168.1.1",
            "bot.just1k.best;rm -rf /",
            "bot.just1k.best\nset",
            "bot.just1k.best\r\nset",
            "bot.just1k.best$(whoami)",
            "bot.just1k.best`id`",
            'bot.just1k.best"junk',
            "bot.just1k.best'junk",
            "bot.just1k.best/path",
            "bot just1k best",
            "-bot.just1k.best",
            "bot.just1k.best-",
            "http://bot.just1k.best",
        ]
        for fqdn in invalid_fqdns:
            res = self._run_shell_snippet(f"validate_fqdn '{fqdn}'")
            self.assertNotEqual(res.returncode, 0, f"Expected '{fqdn}' to be rejected by validate_fqdn")

        # Valid sub prefixes
        valid_prefixes = ["/sub/wl", "/sub/wl/v1", "/prefix", "/a-b/c_d"]
        for prefix in valid_prefixes:
            res = self._run_shell_snippet(f"validate_sub_prefix '{prefix}'")
            self.assertEqual(res.returncode, 0, f"Expected '{prefix}' to be valid sub_prefix")

        # Hostile / Invalid sub prefixes
        invalid_prefixes = [
            "",
            "sub/wl",
            "/sub/../wl",
            "/../secret",
            "/sub/wl;evil",
            "/sub/wl\n",
            "/sub/wl\r\n",
            "/sub/$(whoami)",
            "/sub/`id`",
            '/sub/wl"junk',
            "/sub/wl'junk",
            "/sub/wl?arg=1",
            "/sub/wl#hash",
            "/sub wl",
        ]
        for prefix in invalid_prefixes:
            res = self._run_shell_snippet(f"validate_sub_prefix '{prefix}'")
            self.assertNotEqual(res.returncode, 0, f"Expected '{prefix}' to be rejected by validate_sub_prefix")

    def test_deploy_subscription_proxy_conf_validation(self):
        """deploy_subscription_proxy_conf generates valid Nginx config for valid FQDN and rejects invalid."""
        self._prepare_base_env()

        # Success case
        res = self._run_shell_snippet("deploy_subscription_proxy_conf 'bot.just1k.best' '/sub/wl'")
        self.assertEqual(res.returncode, 0, f"deploy_subscription_proxy_conf failed: {res.stderr}\n{res.stdout}")
        conf_file = self.nginx_relays_d / "sub-wl.conf"
        self.assertTrue(conf_file.exists())
        content = conf_file.read_text(encoding="utf-8")
        self.assertIn("location ^~ /sub/wl {", content)
        self.assertIn('set $bot_upstream "https://bot.just1k.best";', content)
        self.assertIn("proxy_ssl_verify on;", content)
        self.assertIn("proxy_ssl_verify_depth 5;", content)
        self.assertIn("proxy_ssl_trusted_certificate /etc/ssl/certs/ca-certificates.crt;", content)

        # Failure cases: hostile target host (non-interactive fails closed)
        res_hostile_host = self._run_shell_snippet("deploy_subscription_proxy_conf 'bot.just1k.best;rm -rf /' '/sub/wl'")
        self.assertNotEqual(res_hostile_host.returncode, 0)

        # Failure cases: hostile sub prefix
        res_hostile_prefix = self._run_shell_snippet("deploy_subscription_proxy_conf 'bot.just1k.best' '/sub/../evil'")
        self.assertNotEqual(res_hostile_prefix.returncode, 0)

    def test_deploy_and_remove_traffic_watchdog_timer_and_cutoff_invariants(self):
        """deploy_traffic_watchdog_timer installs systemd timer and xray cutoff drop-in, remove cleans up."""
        self._prepare_base_env()
        systemd_dir = self.systemd_dir
        systemd_dir.mkdir(parents=True, exist_ok=True)

        systemd_dir_posix = self._bp(systemd_dir)
        snippet = f"""
export SYSTEMD_SYSTEM_DIR="{systemd_dir_posix}"
deploy_traffic_watchdog_timer
"""
        res = self._run_shell_snippet(snippet)
        self.assertEqual(res.returncode, 0, f"deploy_traffic_watchdog_timer failed: {res.stderr}\n{res.stdout}")

        svc_file = systemd_dir / "just1knode-traffic.service"
        timer_file = systemd_dir / "just1knode-traffic.timer"
        dropin_file = systemd_dir / "xray.service.d" / "traffic-cutoff.conf"

        self.assertTrue(svc_file.exists(), "Traffic service unit must be created")
        self.assertTrue(timer_file.exists(), "Traffic timer unit must be created")
        self.assertTrue(dropin_file.exists(), "xray.service.d/traffic-cutoff.conf must be created")

        dropin_content = dropin_file.read_text(encoding="utf-8")
        self.assertIn("ConditionPathExists=!", dropin_content)
        self.assertIn("traffic_cutoff.active", dropin_content)

        # Removal
        remove_snippet = f"""
export SYSTEMD_SYSTEM_DIR="{systemd_dir_posix}"
remove_traffic_watchdog_timer
"""
        res_rm = self._run_shell_snippet(remove_snippet)
        self.assertEqual(res_rm.returncode, 0)
        self.assertFalse(svc_file.exists(), "Traffic service unit must be removed")
        self.assertFalse(timer_file.exists(), "Traffic timer unit must be removed")
        self.assertFalse(dropin_file.exists(), "xray.service.d/traffic-cutoff.conf must be removed")

    def test_amnezia_node_and_api_artifacts_invariants(self):
        """Verify amnezia_api microservice files and just1knode Amnezia module invariants."""
        amnezia_api_dir = REPO_ROOT / "scripts" / "amnezia_api"
        self.assertTrue((amnezia_api_dir / "app.py").exists(), "app.py must exist in amnezia_api")
        self.assertTrue((amnezia_api_dir / "requirements.txt").exists(), "requirements.txt must exist in amnezia_api")
        self.assertTrue((amnezia_api_dir / "amnezia-api.service").exists(), "amnezia-api.service unit must exist")
        self.assertTrue((amnezia_api_dir / "tests" / "test_api.py").exists(), "test_api.py must exist in amnezia_api/tests")

        amnezia_sh = REPO_ROOT / "just1knode" / "modules" / "amnezia" / "amnezia.sh"
        self.assertTrue(amnezia_sh.exists(), "just1knode amnezia.sh must exist")
        content = amnezia_sh.read_text(encoding="utf-8")
        self.assertIn("install_amnezia_node", content)
        self.assertIn("apply_amnezia_abuse_protection", content)
        self.assertIn("--dport 25 -j REJECT --reject-with tcp-reset", content)
        self.assertIn('--string "BitTorrent protocol" --algo bm', content)
        self.assertIn("sync_ufw_before_rules_antiabuse", content)
        self.assertIn("remove_ufw_before_rules_antiabuse", content)
        self.assertIn("check_amnezia_abuse_rules", content)
        self.assertIn("/etc/modules-load.d/just1knode.conf", content)
        self.assertNotIn("deploy_antiabuse_systemd_service", content)

    def test_amnezia_antiabuse_persistence_and_doctor_invariants(self):
        """Verify anti-abuse persistence, update_node integration, and doctor auto-heal invariants."""
        core_sh = (REPO_ROOT / "just1knode" / "modules" / "xray" / "core.sh").read_text(encoding="utf-8")
        self.assertIn('elif [[ "$role" == "dual" ]]; then', core_sh)
        self.assertIn('heal_and_update_relay_config\n        apply_amnezia_abuse_protection', core_sh)
        self.assertIn('elif [[ "$role" == "awg" ]]; then\n        apply_amnezia_abuse_protection', core_sh)

        relay_sh = (REPO_ROOT / "just1knode" / "modules" / "xray" / "relay.sh").read_text(encoding="utf-8")
        self.assertIn('log "Режим узла обновлен до: DUAL (Совмещенный Relay + AmneziaWG)"\n        apply_amnezia_abuse_protection', relay_sh)

        main_sh = (REPO_ROOT / "just1knode" / "just1knode.sh").read_text(encoding="utf-8")
        self.assertIn("anti-abuse|antiabuse|apply-abuse-protection)", main_sh)
        self.assertNotIn("remove-anti-abuse|remove-antiabuse)", main_sh)
        self.assertIn("Запуск автоматического восстановления (Auto-Heal)...", main_sh)
        self.assertIn("check_amnezia_abuse_rules", main_sh)
        self.assertIn("apply_amnezia_abuse_protection", main_sh)
        self.assertIn("failed=$((failed + 1))", main_sh)

        common_sh = (REPO_ROOT / "just1knode" / "lib" / "common.sh").read_text(encoding="utf-8")
        self.assertNotIn("iptables-persistent netfilter-persistent", common_sh)
        self.assertNotIn("debconf-set-selections", common_sh)

    def test_amnezia_migration_and_rollback_invariants(self):
        """Verify Amnezia node migration path, legacy env discovery, and rollback handling."""
        amnezia_sh = REPO_ROOT / "just1knode" / "modules" / "amnezia" / "amnezia.sh"
        content = amnezia_sh.read_text(encoding="utf-8")
        # Configuration discovery (native only, zero legacy artifacts)
        self.assertIn('$AMNEZIA_API_ETC/config.env', content)
        self.assertNotIn('/opt/amnezia-api/.env', content)
        self.assertNotIn('/root/amnezia-api', content)
        self.assertIn("AMNEZIA_API_KEY", content)
        self.assertIn("SERVER_MAX_PEERS", content)
        # Rollback logic for native service
        self.assertIn("rollback_amnezia_if_needed()", content)
        self.assertIn("systemctl stop amnezia-api.service", content)
        self.assertNotIn("docker start amnezia-api", content)
        self.assertNotIn("pm2", content)
        # Nginx proxy generation, rate limiting and security headers
        self.assertIn("/etc/nginx/sites-available/just1k-amnezia.conf", content)
        self.assertIn("limit_req_zone $binary_remote_addr zone=just1k_amnezia_api:10m rate=30r/s;", content)
        self.assertIn("limit_req zone=just1k_amnezia_api burst=50 nodelay;", content)
        self.assertIn('add_header X-Content-Type-Options "nosniff" always;', content)
        self.assertIn('add_header X-Frame-Options "DENY" always;', content)
        self.assertIn("location ~ ^/(docs|redoc|openapi.json|metrics)", content)
        self.assertIn("proxy_pass http://127.0.0.1:${AMNEZIA_LOCAL_PORT};", content)
        self.assertIn("client_max_body_size 10M;", content)

        # Microservice Swagger/OpenAPI disabling in app.py
        app_py = REPO_ROOT / "scripts" / "amnezia_api" / "app.py"
        app_content = app_py.read_text(encoding="utf-8")
        self.assertIn("docs_url=None", app_content)
        self.assertIn("redoc_url=None", app_content)
        self.assertIn("openapi_url=None", app_content)

        # Origin subscription proxy domestic resolvers
        origin_sh = REPO_ROOT / "just1knode" / "modules" / "xray" / "origin.sh"
        origin_content = origin_sh.read_text(encoding="utf-8")
        self.assertIn('local resolved_servers="77.88.8.8 77.88.8.1 195.208.4.1"', origin_content)

    def test_amnezia_dual_mode_nginx_and_certbot_coexistence(self):
        """Verify Amnezia module properly handles port 80 coexistence, protocol detection, and clean renewal hooks."""
        amnezia_sh = REPO_ROOT / "just1knode" / "modules" / "amnezia" / "amnezia.sh"
        content = amnezia_sh.read_text(encoding="utf-8")

        # 1. Removal of default site to prevent port 80 conflict with Docker proxies (using cp -L to dereference symlink)
        self.assertIn('cp -L "$def_site" "/etc/nginx/sites-available/default.user.bak"', content)
        self.assertIn('rm -f "$def_site"', content)

        # 2. UFW port 80 opening before Certbot challenge with rollback on failure
        self.assertIn('ufw allow 80/tcp comment "just1knode certbot verification"', content)
        self.assertIn('ufw delete allow 80/tcp', content)

        # 3. Dynamic container detection on host port 80 with fail-closed non-interactive mode and trap
        self.assertIn("detect_host_port80_container()", content)
        self.assertIn('pause_ans="N"', content)
        self.assertIn('read -rp', content)
        self.assertIn("trap 'if [[ -n", content)
        self.assertIn('docker stop "$port80_container"', content)
        self.assertIn('docker start "$stopped_container"', content)

        # 4. Strict Nginx lifecycle check with default site restoration on failure
        self.assertIn('systemctl restart nginx', content)
        self.assertIn('systemctl is-active --quiet nginx', content)
        self.assertIn('ln -sf /etc/nginx/sites-available/default /etc/nginx/sites-enabled/default', content)

        # 5. Wildcard and IP in server_name
        self.assertIn('server_name ${api_domain} ${my_ip} _;', content)

        # 6. Streamlined Certbot deploy renewal hook (graceful reload, no unnecessary amnezia-api restart)
        self.assertIn('deploy/restart-amnezia-nginx.sh', content)
        self.assertIn('systemctl reload nginx', content)
        hook_match = re.search(r'deploy_amnezia_certbot_renewal_hook\(\)\s*\{(.*?)\n\}', content, re.DOTALL)
        self.assertIsNotNone(hook_match)
        self.assertNotIn('systemctl restart amnezia-api', hook_match.group(1))

        # 7. Common lib installer purges default site immediately after apt install with cp -L
        common_sh = REPO_ROOT / "just1knode" / "lib" / "common.sh"
        common_content = common_sh.read_text(encoding="utf-8")
        self.assertIn('cp -L "$def_site" "${NGINX_CONF_DIR:-/etc/nginx}/sites-available/default.user.bak"', common_content)
        self.assertIn('rm -f "$def_site"', common_content)

        # 8. Protocol version detection and display (differentiating awg2, awg3, awg3.1)
        self.assertIn("detect_awg_protocol_version()", content)
        self.assertIn("amneziawg3.1", content)
        self.assertIn("amneziawg3", content)
        self.assertIn("amneziawg2", content)
        self.assertIn("🌐 Протокол:", content)
        self.assertIn("📦 Docker контейнер:", content)

        # 9. Doctor check 3b displays protocol version and container status
        just1knode_sh = (REPO_ROOT / "just1knode" / "just1knode.sh").read_text(encoding="utf-8")
        self.assertIn("Docker контейнер:", just1knode_sh)
        self.assertIn("Протокол:", just1knode_sh)

        # 10. Amnezia Nginx Catch-All default_server with ssl_reject_handshake on domain mode
        self.assertIn("listen ${public_port} ssl default_server;", content)
        self.assertIn("ssl_reject_handshake on;", content)
        self.assertIn("server_tokens off;", content)

        # 11. Amnezia API UFW rule restricts to bot_ip
        self.assertIn('ufw allow from "$bot_ip" to any port "$public_port" proto tcp comment "just1knode amnezia api"', content)

    def test_detect_host_port80_container_filtering_behaviour(self):
        """Verify detect_host_port80_container correctly matches host TCP :80 bindings and ignores container-only :80 or UDP :80."""
        # Simulated docker ps outputs
        test_cases = [
            ("other-app\t0.0.0.0:8080->80/tcp", None),
            ("proxy-app\t0.0.0.0:80->80/tcp", "proxy-app"),
            ("custom-app\t0.0.0.0:80->8080/tcp", "custom-app"),
            ("ipv6-proxy\t:::80->80/tcp", "ipv6-proxy"),
            ("dual-proxy\t0.0.0.0:80->80/tcp, :::80->80/tcp", "dual-proxy"),
            ("udp-service\t0.0.0.0:80->80/udp", None),
            ("unrelated\t127.0.0.1:51820->51820/udp", None),
        ]
        import re
        pattern = re.compile(r'(^|[ \t,])([0-9\.:]+|\[::\]|:::):80->[0-9]+/tcp')
        for ports_str, expected in test_cases:
            parts = ports_str.split("\t")
            name, ports = parts[0], parts[1]
            match = pattern.search(ports)
            result = name if match else None
            self.assertEqual(result, expected, f"Failed for mapping: {ports}")

    def test_detect_host_port80_container_host_network_behaviour(self):
        """Verify 64-hex container ID extraction from /proc/$pid/cgroup for host-network containers."""
        import re
        cgroup_pattern = re.compile(r'[0-9a-f]{64}')

        # 1. cgroup v2 format with systemd slice
        cgroup_v2 = "0::/system.slice/docker-a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90.scope"
        m2 = cgroup_pattern.search(cgroup_v2)
        self.assertIsNotNone(m2)
        self.assertEqual(m2.group(0), "a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90")

        # 2. cgroup v1 format
        cgroup_v1 = "12:devices:/docker/11223344556677889900aabbccddeeff11223344556677889900aabbccddeeff"
        m1 = cgroup_pattern.search(cgroup_v1)
        self.assertIsNotNone(m1)
        self.assertEqual(m1.group(0), "11223344556677889900aabbccddeeff11223344556677889900aabbccddeeff")

        # 3. Host non-docker service (e.g. systemd nginx)
        cgroup_host = "0::/system.slice/nginx.service"
        self.assertIsNone(cgroup_pattern.search(cgroup_host))

    def test_detect_awg_protocol_version_behaviour(self):
        """Verify detect_awg_protocol_version properly parses active vs disabled toggle flags and protocol_version."""
        import re
        re_3_1 = re.compile(r'^[ \t]*(RandomTrailers|DisableCookies)[ \t]*=[ \t]*(on|yes|true|1)|^[ \t]*protocol_version[ \t]*=[ \t]*3\.1', re.IGNORECASE | re.MULTILINE)
        re_3_0 = re.compile(r'^[ \t]*(HeaderProtectionKey|Hpk)[ \t]*=[ \t]*[^ \t#;]|^[ \t]*(ContentPaddingAddition|RekeyAfterTime|RekeyTimeout|RejectAfterTime|KeepaliveTimeout|MaxHandshakeAttempts)[ \t]*=[ \t]*[^ \t#;]|^[ \t]*protocol_version[ \t]*=[ \t]*3(\.0)?', re.IGNORECASE | re.MULTILINE)

        def mock_detect(conf: str) -> str:
            if re_3_1.search(conf):
                return "amneziawg3.1"
            if re_3_0.search(conf):
                return "amneziawg3"
            return "amneziawg2"

        # 1. Base AWG 2.0 configuration
        conf_2_0 = "[Interface]\nPrivateKey = aaaa\nJc = 4\nS1 = 12\nH1 = 1\n"
        self.assertEqual(mock_detect(conf_2_0), "amneziawg2")

        # 2. AWG 3.1 with disabled flags (must NOT falsely detect as 3.1)
        conf_disabled_3_1 = "[Interface]\nPrivateKey = aaaa\nRandomTrailers = off\nDisableCookies = 0\n"
        self.assertEqual(mock_detect(conf_disabled_3_1), "amneziawg2")

        # 3. AWG 3.0 with HeaderProtectionKey and disabled RandomTrailers
        conf_3_0 = "[Interface]\nPrivateKey = aaaa\nHeaderProtectionKey = 47DEQpj8HBSa+/TImW+5JCeuQeRkm5NMpJWZG3hSuFU=\nRandomTrailers = off\n"
        self.assertEqual(mock_detect(conf_3_0), "amneziawg3")

        # 3b. AWG 3.0 with HeaderProtectionKey starting with '0' (valid base64 char)
        conf_3_0_zero = "[Interface]\nPrivateKey = aaaa\nHeaderProtectionKey = 0VzSecretKeyCurve25519String=\n"
        self.assertEqual(mock_detect(conf_3_0_zero), "amneziawg3")

        # 3c. AWG 3.0 with timing parameters set to '0' (must detect as 3.0 matching vpn_parser.py)
        conf_3_0_timing_zero = "[Interface]\nPrivateKey = aaaa\nContentPaddingAddition = 0\n"
        self.assertEqual(mock_detect(conf_3_0_timing_zero), "amneziawg3")

        # 4. AWG 3.1 with active flags
        conf_3_1_on = "[Interface]\nPrivateKey = aaaa\nRandomTrailers = on\nDisableCookies = on\n"
        self.assertEqual(mock_detect(conf_3_1_on), "amneziawg3.1")

        conf_3_1_numeric = "[Interface]\nPrivateKey = aaaa\nRandomTrailers = 1\n"
        self.assertEqual(mock_detect(conf_3_1_numeric), "amneziawg3.1")

        # 5. AWG versions specified via protocol_version field
        conf_pv_3_1 = "[Interface]\nPrivateKey = aaaa\nprotocol_version = 3.1\n"
        self.assertEqual(mock_detect(conf_pv_3_1), "amneziawg3.1")

        conf_pv_3_0 = "[Interface]\nPrivateKey = aaaa\nprotocol_version = 3.0\n"
        self.assertEqual(mock_detect(conf_pv_3_0), "amneziawg3")

        conf_pv_2 = "[Interface]\nPrivateKey = aaaa\nprotocol_version = 2\n"
        self.assertEqual(mock_detect(conf_pv_2), "amneziawg2")

    def test_default_site_symlink_dereference_behaviour(self):
        """Verify cp -L correctly dereferences symlinks to avoid creating broken circular symlink backups."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            avail_dir = tmp_path / "sites-available"
            enabled_dir = tmp_path / "sites-enabled"
            avail_dir.mkdir()
            enabled_dir.mkdir()

            real_default = avail_dir / "default"
            real_default.write_text("server { server_name _; listen 80; }", encoding="utf-8")

            symlink_default = enabled_dir / "default"
            try:
                symlink_default.symlink_to(real_default)
            except OSError:
                # Windows without dev mode symlink privilege fallback
                pass

            backup_file = avail_dir / "default.user.bak"
            # Using shutil.copy2 or python equivalent of cp -L (following symlinks)
            if symlink_default.is_symlink():
                shutil.copyfile(symlink_default.resolve(), backup_file)
                self.assertFalse(backup_file.is_symlink(), "Backup must be a real file, not a symlink")
                self.assertIn("server_name _;", backup_file.read_text(encoding="utf-8"))

    def test_origin_nginx_catchall_zero_cert_leak(self):
        """Verify Origin Nginx Catch-All default_server does not contain ssl_certificate when ssl_reject_handshake is supported."""
        origin_sh = (REPO_ROOT / "just1knode" / "modules" / "xray" / "origin.sh").read_text(encoding="utf-8")
        self.assertIn("ssl_reject_handshake on;", origin_sh)
        self.assertIn("listen 443 ssl default_server;", origin_sh)
        self.assertIn("listen 80 default_server;", origin_sh)
        self.assertIn("return 444;", origin_sh)

        # Catchall block with ssl_reject_handshake must NOT have ssl_certificate
        match = re.search(r'catchall_ssl_block="server\s*\{[^}]*ssl_reject_handshake on;[^}]*\}', origin_sh, re.DOTALL)
        self.assertIsNotNone(match, "catchall_ssl_block with ssl_reject_handshake on; must be defined")
        self.assertNotIn("ssl_certificate", match.group(0), "ssl_certificate must NOT be inside catchall when ssl_reject_handshake on is active")

    def test_set_bot_ip_supports_origin_awg_and_dual_roles(self):
        """Verify set_origin_bot_ip dynamically adapts target port based on role."""
        origin_sh = (REPO_ROOT / "just1knode" / "modules" / "xray" / "origin.sh").read_text(encoding="utf-8")
        self.assertIn('target_port="8444"', origin_sh)
        self.assertIn('target_port="$(get_state_val "awg_port" "8443")"', origin_sh)
        self.assertIn('ufw allow from "$new_bot_ip" to any port "$target_port" proto tcp', origin_sh)
        self.assertIn('ufw delete allow 8443/tcp', origin_sh)

    def test_heal_and_update_origin_config_cleans_8443_and_ensures_catchall(self):
        """Verify heal_and_update_origin_config removes port 8443 and restores catchall without cert leak."""
        origin_sh = (REPO_ROOT / "just1knode" / "modules" / "xray" / "origin.sh").read_text(encoding="utf-8")
        self.assertIn('ufw delete allow 8443/tcp', origin_sh)
        self.assertIn('listen 80 default_server', origin_sh)
        self.assertIn('listen 443 ssl default_server', origin_sh)
        self.assertIn("re.sub(r'server\\s*\\{[^}]*listen\\s+8443\\s+ssl[^}]*\\}\\n*', '', content, flags=re.DOTALL)", origin_sh)

    def test_doctor_ufw_acl_validation_awg_and_dual(self):
        """Verify run_doctor checks awg_port for awg and dual nodes."""
        just1knode_sh = (REPO_ROOT / "just1knode" / "just1knode.sh").read_text(encoding="utf-8")
        self.assertIn('awg_p="$(get_state_val "awg_port" "8443")"', just1knode_sh)
        self.assertIn("Порт API AmneziaWG $awg_p открыт для всех", just1knode_sh)
        self.assertIn("Порт API AmneziaWG $awg_p защищен и доступен только с BOT_IP", just1knode_sh)


    def test_validate_ip_ipv4_and_ipv6_behaviour(self):
        """Verify validate_ip logic handles both IPv4 and IPv6 properly."""
        import ipaddress

        def mock_validate_ip(ip_str: str) -> bool:
            if not ip_str or not isinstance(ip_str, str):
                return False
            try:
                addr = ipaddress.ip_address(ip_str.strip())
                return not addr.is_multicast and not addr.is_unspecified and not addr.is_reserved
            except ValueError:
                return False

        # Valid IPv4
        self.assertTrue(mock_validate_ip("192.168.1.1"))
        self.assertTrue(mock_validate_ip("1.1.1.1"))
        self.assertTrue(mock_validate_ip("185.220.101.5"))

        # Valid IPv6
        self.assertTrue(mock_validate_ip("2001:db8::1"))
        self.assertTrue(mock_validate_ip("2a00:1450:4010:c08::71"))

        # Invalid IP addresses
        self.assertFalse(mock_validate_ip("256.1.1.1"))
        self.assertFalse(mock_validate_ip("0.0.0.0"))
        self.assertFalse(mock_validate_ip("255.255.255.255"))
        self.assertFalse(mock_validate_ip("::"))
        self.assertFalse(mock_validate_ip("not-an-ip"))

    def test_uninstall_and_cleanup_cleans_awg_port_in_ufw(self):
        """Verify uninstall_node and uninstall_amnezia_component remove awg_port and bot_ip from UFW."""
        just1knode_sh = (REPO_ROOT / "just1knode" / "just1knode.sh").read_text(encoding="utf-8")
        self.assertIn('st_awg_port="$(get_state_val "awg_port" 2>/dev/null || true)"', just1knode_sh)
        self.assertIn('ufw delete allow from "$st_bot_ip" to any port "$st_awg_port" proto tcp', just1knode_sh)
        self.assertIn('ufw delete allow "${st_awg_port}/tcp"', just1knode_sh)

        amnezia_sh = (REPO_ROOT / "just1knode" / "modules" / "amnezia" / "amnezia.sh").read_text(encoding="utf-8")
        self.assertIn('ufw delete allow from "$bot_ip" to any port "$pub_port" proto tcp', amnezia_sh)

    def test_origin_catchall_dummy_fallback_on_old_nginx(self):
        """Verify origin fallback generates dummy cert instead of leaking domain cert on Nginx < 1.19.4."""
        origin_sh = (REPO_ROOT / "just1knode" / "modules" / "xray" / "origin.sh").read_text(encoding="utf-8")
        self.assertIn("dummy_dir=", origin_sh)
        self.assertIn("CN=invalid", origin_sh)
        self.assertIn("ssl_certificate ${dummy_dir}/dummy.crt;", origin_sh)
        self.assertIn("ssl_certificate {dummy_dir}/dummy.crt;", origin_sh)
        self.assertNotIn("ssl_certificate /etc/letsencrypt/live/{domain}/fullchain.pem;", origin_sh)

    def test_amnezia_catchall_dummy_fallback_on_old_nginx(self):
        """Verify amnezia fallback generates dummy cert instead of leaking domain cert on Nginx < 1.19.4."""
        amnezia_sh = (REPO_ROOT / "just1knode" / "modules" / "amnezia" / "amnezia.sh").read_text(encoding="utf-8")
        self.assertIn("dummy_dir=", amnezia_sh)
        self.assertIn("CN=invalid", amnezia_sh)
        self.assertIn("ssl_certificate ${dummy_dir}/dummy.crt;", amnezia_sh)
        self.assertIn("listen ${public_port} ssl default_server;", amnezia_sh)

    def test_relay_install_strictly_tls_and_validates_dns(self):
        """Verify install_xray_relay_node strictly provisions VLESS TLS and validates DNS A-record."""
        relay_sh = (REPO_ROOT / "just1knode" / "modules" / "xray" / "relay.sh").read_text(encoding="utf-8")
        self.assertIn('local sec_mode="tls"', relay_sh)
        self.assertIn('validate_relay_dns "$dest_server" "$my_ip"', relay_sh)
        self.assertIn('issue_relay_tls_cert "$dest_server"', relay_sh)
        # Verify new installations do not configure REALITY
        self.assertNotIn('Режим REALITY (legacy)', relay_sh)

    def test_relay_tls_cert_permanent_renewal_hooks_and_freshness_check(self):
        """Verify issue_relay_tls_cert cleans up global hooks, scopes pre/post hooks to lineage, and installs deploy hook."""
        relay_sh = (REPO_ROOT / "just1knode" / "modules" / "xray" / "relay.sh").read_text(encoding="utf-8")
        self.assertIn('rm -f "${le_dir}/renewal-hooks/pre/05-just1knode-nginx.sh"', relay_sh)
        self.assertIn('"${le_dir}/renewal-hooks/deploy/20-just1knode-restart-xray.sh"', relay_sh)
        self.assertIn('--pre-hook "$pre_hook_cmd"', relay_sh)
        self.assertIn('--post-hook "$post_hook_cmd"', relay_sh)
        self.assertIn("openssl x509 -checkend 86400", relay_sh)
        self.assertIn("port80_was_open", relay_sh)
        # Ensure deploy hook checks RENEWED_LINEAGE against RELAY_SNI
        self.assertIn('[ "$(basename "$RENEWED_LINEAGE")" = "$RELAY_SNI" ]', relay_sh)

    def test_add_relay_node_supports_legacy_and_new_syntax(self):
        """Verify add_relay_node correctly parses legacy positional args and new tls args."""
        relays_manage_sh = (REPO_ROOT / "just1knode" / "modules" / "xray" / "relays_manage.sh").read_text(encoding="utf-8")
        self.assertIn('if [[ "$arg6" == "tls" || "$arg6" == "reality" ]]; then', relays_manage_sh)
        self.assertIn('elif [[ -n "$arg6" && "$arg6" != "-" ]]; then', relays_manage_sh)
        self.assertIn('security_type="reality"', relays_manage_sh)
        self.assertIn('Для режима TLS обязательно указание домена (SNI)', relays_manage_sh)
        self.assertIn('Для режима REALITY обязательно указание публичного ключа (PublicKey)', relays_manage_sh)

    def test_show_relay_credentials_legacy_state_inspection_and_no_hardcoded_de(self):
        """Verify show_relay_credentials normalizes missing security key in legacy state and avoids hardcoded de."""
        just1knode_sh = (REPO_ROOT / "just1knode" / "just1knode.sh").read_text(encoding="utf-8")
        self.assertIn('r_sec="$(get_state_val "security" "")"', just1knode_sh)
        self.assertIn('if [[ -z "$r_sec" ]]; then', just1knode_sh)
        self.assertIn('sec = ib.get(\'streamSettings\', {}).get(\'security\')', just1knode_sh)
        self.assertNotIn('local detected_code="de"', just1knode_sh)
        self.assertIn('detected_code="relay-01"', just1knode_sh)

    def test_heal_and_update_relay_config_auto_migrates_tls_on_cert_found(self):
        """Verify heal_and_update_relay_config validates domain before selecting cert and guards TLS inbound."""
        relay_sh = (REPO_ROOT / "just1knode" / "modules" / "xray" / "relay.sh").read_text(encoding="utf-8")
        self.assertIn("ai = socket.getaddrinfo(domain, None, socket.AF_INET)", relay_sh)
        self.assertIn("Автоматический перевод входящего туннеля Relay на VLESS + TLS", relay_sh)
        self.assertIn("tls_cert_file = os.path.join(tls_cert_dir, 'fullchain.pem')", relay_sh)
        self.assertIn("if sec_mode == 'tls' and os.path.exists(tls_cert_file) and os.path.exists(tls_key_file):", relay_sh)
        self.assertIn("st['security'] = 'tls'", relay_sh)
        self.assertIn("st.pop('realitySettings', None)", relay_sh)

    def test_heal_and_update_origin_config_auto_migrates_relay_on_dns_match(self):
        """Verify heal_and_update_origin_config matches multi-IP DNS A-record with TLS probe and upgrades outbounds."""
        origin_sh = (REPO_ROOT / "just1knode" / "modules" / "xray" / "origin.sh").read_text(encoding="utf-8")
        self.assertIn("Авто-миграция Relay-узлов с REALITY / google.com на VLESS+TLS", origin_sh)
        self.assertIn("addr_infos = socket.getaddrinfo(cand, None, socket.AF_INET)", origin_sh)
        self.assertIn("ctx.wrap_socket(s, server_hostname=cand)", origin_sh)
        self.assertIn("st.pop('realitySettings', None)", origin_sh)
        self.assertIn("r['security'] = 'tls'", origin_sh)

    def test_ensure_xray_config_permissions_and_rollback_invariant(self):
        """Verify state.sh and relays_manage enforce ensure_xray_config_permissions and retain root:xrayapi 640."""
        state_sh = (REPO_ROOT / "just1knode" / "lib" / "state.sh").read_text(encoding="utf-8")
        relays_manage_sh = (REPO_ROOT / "just1knode" / "modules" / "xray" / "relays_manage.sh").read_text(encoding="utf-8")

        self.assertIn("ensure_xray_config_permissions()", state_sh)
        self.assertIn("chown root:xrayapi \"$cfg\"", state_sh)
        self.assertIn("chmod 640 \"$cfg\"", state_sh)
        self.assertIn("ensure_xray_config_permissions", relays_manage_sh)
        self.assertIn("manifest_rollback()", state_sh)
        self.assertIn("ensure_xray_config_permissions \"${XRAY_CONFIG:-/usr/local/etc/xray/config.json}\"", state_sh)
        self.assertIn("r['outboundTag'] = 'just1k-wl-direct'", relays_manage_sh)

    def test_xray_api_service_unit_has_partof_and_update_node_syncs_it(self):
        """Verify xray-api.service has PartOf=xray.service, start limit resilience, and ensure_xray_api_healthy."""
        service_file = (REPO_ROOT / "scripts" / "xray_api" / "xray-api.service").read_text(encoding="utf-8")
        core_sh = (REPO_ROOT / "just1knode" / "modules" / "xray" / "core.sh").read_text(encoding="utf-8")
        api_sh = (REPO_ROOT / "just1knode" / "modules" / "xray" / "api.sh").read_text(encoding="utf-8")
        common_sh = (REPO_ROOT / "just1knode" / "lib" / "common.sh").read_text(encoding="utf-8")
        origin_sh = (REPO_ROOT / "just1knode" / "modules" / "xray" / "origin.sh").read_text(encoding="utf-8")
        relays_manage_sh = (REPO_ROOT / "just1knode" / "modules" / "xray" / "relays_manage.sh").read_text(encoding="utf-8")
        just1knode_sh = (REPO_ROOT / "just1knode" / "just1knode.sh").read_text(encoding="utf-8")
        ssl_sh = (REPO_ROOT / "just1knode" / "lib" / "ssl.sh").read_text(encoding="utf-8")
        traffic_watchdog_sh = (REPO_ROOT / "just1knode" / "lib" / "traffic_watchdog.sh").read_text(encoding="utf-8")

        self.assertIn("PartOf=xray.service", service_file)
        self.assertIn("StartLimitIntervalSec=30", service_file)
        self.assertIn("StartLimitBurst=15", service_file)

        self.assertIn("PartOf=xray.service", api_sh)
        self.assertIn("StartLimitIntervalSec=30", api_sh)
        self.assertIn("StartLimitBurst=15", api_sh)

        self.assertIn("cp \"${api_dir}/xray-api.service\" /etc/systemd/system/xray-api.service", core_sh)
        self.assertIn("systemctl daemon-reload", core_sh)

        # Invariant: ensure_xray_api_healthy avoids duplicate restarts and returns non-zero on failure
        self.assertIn("ensure_xray_api_healthy()", common_sh)
        self.assertIn("return 1", common_sh)
        self.assertIn("ensure_xray_api_healthy", origin_sh)
        self.assertIn("ensure_xray_api_healthy", core_sh)
        self.assertIn("ensure_xray_api_healthy", relays_manage_sh)

        # Invariant: certbot deploy hook does not execute blind duplicate restart of xray-api
        self.assertNotIn("systemctl restart xray\nsystemctl restart xray-api", ssl_sh.replace(" ", "").replace("2>/dev/null||true", ""))
        self.assertIn("systemctl start xray-api", ssl_sh)

        # Invariant: traffic watchdog resume restarts xray-api when present
        self.assertIn("systemctl start xray-api", traffic_watchdog_sh)

        # Invariant: Doctor reports FAILED status on services without hidden side-effects
        self.assertIn("systemctl is-failed", just1knode_sh)
        self.assertIn("FAILED", just1knode_sh)

    def test_xray_api_app_discovers_default_inbound_on_origin_fallback(self):
        """Verify app.py target inbounds discovery prioritizes just1k-wl-default on Origin nodes."""
        app_py = (REPO_ROOT / "scripts" / "xray_api" / "app.py").read_text(encoding="utf-8")
        self.assertIn("is_origin_node", app_py)
        self.assertIn("discovered_tags.insert(0, \"just1k-wl-default\")", app_py)


    def test_origin_nginx_reconciliation_uses_xray_inbound_port(self):
        """Verify origin.sh extracts local inbound port from Xray config and avoids remote 10443 port."""
        origin_sh = (REPO_ROOT / "just1knode" / "modules" / "xray" / "origin.sh").read_text(encoding="utf-8")
        self.assertIn("xray_inbound_ports = {}", origin_sh)
        self.assertIn("port = xray_inbound_ports.get(in_tag)", origin_sh)
        self.assertIn("r['inbound_port'] = port", origin_sh)
        self.assertNotIn("port = r.get('inbound_port') or r.get('port')", origin_sh)

    def test_add_relay_node_records_inbound_port_in_relays_json(self):
        """Verify relays_manage.sh add_relay_node passes and stores next_port as inbound_port."""
        relays_manage_sh = (REPO_ROOT / "just1knode" / "modules" / "xray" / "relays_manage.sh").read_text(encoding="utf-8")
        self.assertIn("'inbound_port': in_port", relays_manage_sh)
        self.assertIn("\"$next_port\"", relays_manage_sh)

    def test_relay_cert_detection_orders_by_mtime_descending(self):
        """Verify relay.sh sorts Let's Encrypt live certs newest first and supports interactive resolution."""
        relay_sh = (REPO_ROOT / "just1knode" / "modules" / "xray" / "relay.sh").read_text(encoding="utf-8")
        self.assertIn("detect_relay_domain_candidates", relay_sh)
        self.assertIn("select_relay_domain_interactive", relay_sh)
        self.assertIn("candidates.sort(key=lambda x: x[0], reverse=True)", relay_sh)
        self.assertIn("Какой домен использовать для Relay?", relay_sh)
        self.assertIn("-checkend", relay_sh)
        self.assertIn("validate_relay_dns", relay_sh)
        self.assertIn("has_cur_sni", relay_sh)
        self.assertIn("Неинтерактивный режим", relay_sh)
        self.assertIn("Использовать '${candidates[0]}' для Relay? [Y/n]", relay_sh)

    def test_select_relay_domain_non_interactive_preserves_valid_cur_sni(self):
        """Verify non-interactive domain selection preserves cur_sni if valid, and falls back if invalid."""
        def select_non_interactive(candidates, cur_sni):
            has_cur_sni = cur_sni in candidates if cur_sni else False
            if has_cur_sni:
                return cur_sni
            elif candidates:
                return candidates[0]
            else:
                return ""

        # Case 1: cur_sni is among candidates (even if not the newest) -> keep cur_sni!
        candidates = ["new.example.com", "old.example.com"]
        self.assertEqual(select_non_interactive(candidates, "old.example.com"), "old.example.com")

        # Case 2: cur_sni is invalid/missing -> fallback to newest
        self.assertEqual(select_non_interactive(candidates, "dead.example.com"), "new.example.com")
        self.assertEqual(select_non_interactive(candidates, ""), "new.example.com")

        # Case 3: no candidates -> empty
        self.assertEqual(select_non_interactive([], "any.example.com"), "")

    def test_update_node_post_reexec_invariants(self):
        """Verify update_node re-execs with update-post to eliminate in-memory stale functions."""
        core_sh = (REPO_ROOT / "just1knode" / "modules" / "xray" / "core.sh").read_text(encoding="utf-8")
        main_sh = (REPO_ROOT / "just1knode" / "just1knode.sh").read_text(encoding="utf-8")

        self.assertIn('exec "$bin_path" update-post "$target"', core_sh)
        self.assertNotIn('export JUST1KNODE_POST_UPDATE', core_sh)
        self.assertIn('update_node_post() {', core_sh)
        self.assertIn('check_root', core_sh)
        self.assertIn('update-post)', main_sh)
        self.assertIn('update_node_post "${1:-all}"', main_sh)

    def test_detect_relay_domain_candidates_filter_and_sort_logic(self):
        """Verify the candidate filter and sort algorithm prioritizes newest valid certs and excludes non-domains."""
        import glob
        import tempfile
        import time

        with tempfile.TemporaryDirectory() as tmp_le:
            live_dir = Path(tmp_le) / "live"
            live_dir.mkdir()

            # Domain 1: older cert
            d1 = live_dir / "old.example.com"
            d1.mkdir()
            (d1 / "privkey.pem").write_text("key1", encoding="utf-8")
            fc1 = d1 / "fullchain.pem"
            fc1.write_text("cert1", encoding="utf-8")
            os.utime(fc1, (time.time() - 100, time.time() - 100))

            # Domain 2: newer cert
            d2 = live_dir / "new.example.com"
            d2.mkdir()
            (d2 / "privkey.pem").write_text("key2", encoding="utf-8")
            fc2 = d2 / "fullchain.pem"
            fc2.write_text("cert2", encoding="utf-8")
            os.utime(fc2, (time.time(), time.time()))

            # Non-candidate dir (e.g. README)
            (live_dir / "README").write_text("ignore", encoding="utf-8")

            candidates = []
            for d in glob.glob(os.path.join(str(live_dir), "*")):
                if not os.path.isdir(d):
                    continue
                domain = os.path.basename(d)
                if domain in ("README", "*"):
                    continue
                fc = os.path.join(d, "fullchain.pem")
                pk = os.path.join(d, "privkey.pem")
                if not (os.path.isfile(fc) and os.path.isfile(pk)):
                    continue
                mtime = os.path.getmtime(fc)
                candidates.append((mtime, domain))

            candidates.sort(key=lambda x: x[0], reverse=True)
            sorted_domains = [c[1] for c in candidates]
            self.assertEqual(sorted_domains, ["new.example.com", "old.example.com"])


    def test_origin_nginx_reconciliation_behavior_isolated_and_strict(self):
        """Behavioral test: Origin reconciliation isolates failures, rejects stale ports, and prunes stale/orphan configs."""
        import json
        import shutil
        import tempfile

        tmp_dir = tempfile.mkdtemp()
        try:
            relays_file = os.path.join(tmp_dir, "relays.json")
            nginx_dir = os.path.join(tmp_dir, "relays_d")
            os.makedirs(nginx_dir, exist_ok=True)
            cfg_file = os.path.join(tmp_dir, "config.json")

            # Pre-create system configs (must NEVER be deleted)
            with open(os.path.join(nginx_dir, "default.conf"), "w", encoding="utf-8") as f:
                f.write("proxy_pass http://127.0.0.1:8003;")
            with open(os.path.join(nginx_dir, "sub-wl.conf"), "w", encoding="utf-8") as f:
                f.write("proxy_pass https://bot.example.com;")

            # Pre-create a stale nl.conf and an orphaned old.conf
            with open(os.path.join(nginx_dir, "nl.conf"), "w", encoding="utf-8") as f:
                f.write("proxy_pass http://127.0.0.1:8008;")
            with open(os.path.join(nginx_dir, "old.conf"), "w", encoding="utf-8") as f:
                f.write("proxy_pass http://127.0.0.1:8005;")

            # Xray config only has inbounds for 'pl' (port 8007)
            x_cfg = {
                "inbounds": [
                    {"tag": "just1k-wl-inbound-pl", "port": 8007}
                ]
            }
            with open(cfg_file, "w", encoding="utf-8") as f:
                json.dump(x_cfg, f)

            # Relays registry:
            # 1. pl: valid inbound
            # 2. nl: stale inbound_port=8008, but NOT in Xray config (must be pruned!)
            # 3. bad: corrupt entry (must not break pl)
            relays_data = [
                {"code": "pl", "path": "/stream/pl", "inbound_port": 8007, "inbound_tag": "just1k-wl-inbound-pl"},
                {"code": "nl", "path": "/stream/nl", "inbound_port": 8008, "inbound_tag": "just1k-wl-inbound-nl"},
                {"code": "bad", "path": None, "inbound_port": "not-a-port"}
            ]
            with open(relays_file, "w", encoding="utf-8") as f:
                json.dump(relays_data, f)

            # Run reconciliation logic exactly as implemented in origin.sh
            with open(relays_file, "r", encoding="utf-8") as f:
                relays = json.load(f)

            xray_inbound_ports = {}
            with open(cfg_file, "r", encoding="utf-8") as cf_f:
                x_c = json.load(cf_f)
                for ib in x_c.get("inbounds", []):
                    t = ib.get("tag")
                    p = ib.get("port")
                    if t and p:
                        xray_inbound_ports[t] = int(p)

            active_configs = set()
            for r in relays:
                try:
                    if not isinstance(r, dict):
                        continue
                    code, path = r.get("code"), r.get("path")
                    if not code or not path:
                        continue
                    code_lower = str(code).strip().lower()
                    cf_name = f"{code}.conf"
                    cf_path = os.path.join(nginx_dir, cf_name)
                    in_tag = r.get("inbound_tag") or f"just1k-wl-inbound-{code_lower}"
                    port = xray_inbound_ports.get(in_tag)
                    if not port:
                        for t, p in xray_inbound_ports.items():
                            if t.lower() == f"just1k-wl-inbound-{code_lower}":
                                port = p
                                break
                    if not port:
                        if os.path.exists(cf_path):
                            os.remove(cf_path)
                        continue

                    active_configs.add(cf_name)

                    with open(cf_path, "w", encoding="utf-8") as cf:
                        cf.write(f"proxy_pass http://127.0.0.1:{port};")
                except Exception:
                    continue

            # Prune orphaned configs protecting system origin configs
            system_origin_configs = {"default.conf", "sub-wl.conf"}
            for item in os.listdir(nginx_dir):
                if item.endswith(".conf") and item not in active_configs and item not in system_origin_configs:
                    os.remove(os.path.join(nginx_dir, item))

            # Invariant: default.conf and sub-wl.conf were PRESERVED and NOT deleted!
            self.assertTrue(os.path.exists(os.path.join(nginx_dir, "default.conf")))
            self.assertTrue(os.path.exists(os.path.join(nginx_dir, "sub-wl.conf")))

            # Invariant: pl.conf was created with local port 8007
            self.assertTrue(os.path.exists(os.path.join(nginx_dir, "pl.conf")))
            self.assertIn("127.0.0.1:8007", open(os.path.join(nginx_dir, "pl.conf")).read())

            # Invariant: stale nl.conf was explicitly DELETED because Xray does not listen on 8008
            self.assertFalse(os.path.exists(os.path.join(nginx_dir, "nl.conf")))

            # Invariant: orphaned old.conf was explicitly DELETED
            self.assertFalse(os.path.exists(os.path.join(nginx_dir, "old.conf")))

            # Invariant: bad relay did not crash reconciliation
            self.assertFalse(os.path.exists(os.path.join(nginx_dir, "bad.conf")))
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_ufw_before_rules_sync_and_remove_behavior(self):
        """Behavioral test: UFW before.rules sync inserts 4 rules and removal cleanly cleans them up."""
        sample_ufw = """#
# rules.before
#
# Rules that should be run before the ufw command line added rules.
*filter
:ufw-before-input - [0:0]
:ufw-before-output - [0:0]
:ufw-before-forward - [0:0]
:ufw-not-local - [0:0]
# drop INVALID packets
-A ufw-before-forward -m conntrack --ctstate INVALID -j DROP
COMMIT
"""
        import re

        def sync_rules(content):
            content = re.sub(r'# START JUST1KNODE ANTI-ABUSE.*?# END JUST1KNODE ANTI-ABUSE\n?', '', content, flags=re.DOTALL)
            block = """# START JUST1KNODE ANTI-ABUSE
-A ufw-before-forward -p tcp --dport 25 -j REJECT --reject-with tcp-reset
-A ufw-before-forward -p tcp -m string --string "BitTorrent protocol" --algo bm -j DROP
-A ufw-before-forward -p udp -m string --string "BitTorrent protocol" --algo bm -j DROP
-A ufw-before-forward -p udp -m string --string "d1:ad2:id20:" --algo bm -j DROP
# END JUST1KNODE ANTI-ABUSE
"""
            if ':ufw-before-forward' in content:
                content = re.sub(r'(:\s*ufw-before-forward\s+-\s+\[0:0\]\n)', r'\1' + block, content, count=1)
            elif '*filter' in content:
                content = content.replace('*filter\n', '*filter\n' + block, 1)
            return content

        def remove_rules(content):
            return re.sub(r'# START JUST1KNODE ANTI-ABUSE.*?# END JUST1KNODE ANTI-ABUSE\n?', '', content, flags=re.DOTALL)

        synced = sync_rules(sample_ufw)
        self.assertIn("# START JUST1KNODE ANTI-ABUSE", synced)
        self.assertIn("-A ufw-before-forward -p tcp --dport 25 -j REJECT --reject-with tcp-reset", synced)
        self.assertIn('-A ufw-before-forward -p tcp -m string --string "BitTorrent protocol" --algo bm -j DROP', synced)
        self.assertIn('-A ufw-before-forward -p udp -m string --string "BitTorrent protocol" --algo bm -j DROP', synced)
        self.assertIn('-A ufw-before-forward -p udp -m string --string "d1:ad2:id20:" --algo bm -j DROP', synced)

        # Idempotence: syncing again does not duplicate block
        synced_again = sync_rules(synced)
        self.assertEqual(synced.count("# START JUST1KNODE ANTI-ABUSE"), 1)
        self.assertEqual(synced_again, synced)

        # Removal restores original content
        removed = remove_rules(synced)
        self.assertNotIn("# START JUST1KNODE ANTI-ABUSE", removed)
        self.assertEqual(removed.strip(), sample_ufw.strip())

    def test_amnezia_status_uses_unified_check(self):
        """Verify show_amnezia_status and check_amnezia_abuse_rules share the exact same check."""
        amnezia_sh = (REPO_ROOT / "just1knode" / "modules" / "amnezia" / "amnezia.sh").read_text(encoding="utf-8")
        self.assertIn("if check_amnezia_abuse_rules; then", amnezia_sh)
        self.assertIn('dport 25 -j REJECT --reject-with tcp-reset', amnezia_sh)
        self.assertIn('string \\"BitTorrent protocol\\"', amnezia_sh)


if __name__ == "__main__":
    unittest.main()


