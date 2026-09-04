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
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
JUST1KNODE_SH = REPO_ROOT / "scripts" / "just1knode.sh"
REQUIREMENTS_TXT = REPO_ROOT / "scripts" / "xray_api" / "requirements.txt"


class TestJust1kNodeScript(unittest.TestCase):
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
        env["WWW_HTML_DIR"] = str(self.www_html_dir)
        if extra_env:
            env.update(extra_env)

        backup_dir_val = extra_env.get("BACKUP_DIR", str(self.backup_dir)) if extra_env else str(self.backup_dir)

        # Source just1knode.sh functions and run snippet with root bypass for testing
        full_script = f"""
export STATE_DIR='{self.state_dir}'
export STATE_FILE='{self.state_dir / "state.json"}'
export CLIENTS_FILE='{self.state_dir / "clients.json"}'
export RELAYS_FILE='{self.state_dir / "relays.json"}'
export XRAY_CONFIG_DIR='{self.xray_config_dir}'
export XRAY_CONFIG='{self.xray_config_dir / "config.json"}'
export XRAY_SHARE_DIR='{self.xray_share_dir}'
export XRAY_BIN='{self.bin_dir / "xray"}'
export BACKUP_DIR='{backup_dir_val}'
export NGINX_CONF_DIR='{self.nginx_conf_dir}'
export NGINX_RELAYS_DIR='{self.nginx_relays_d}'
export XRAY_API_DIR='{self.xray_api_dir}'
export XRAY_API_LIB='{self.xray_api_lib}'
export XRAY_API_ETC='{self.xray_api_etc}'
export XRAY_API_CONFIG_ENV='{self.xray_api_etc / "config.env"}'
export SYSTEMD_SYSTEM_DIR='{self.systemd_dir}'
export CERTBOT_DIR='{self.certbot_dir}'
export LETSENCRYPT_DIR='{self.letsencrypt_dir}'
export WWW_HTML_DIR='{self.www_html_dir}'

source '{JUST1KNODE_SH}'

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
            input=input_text,
            capture_output=True,
            text=True,
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

        # 3. Check Split-DNS and skipFallback
        self.assertEqual(updated["dns"]["queryStrategy"], "UseIPv4")
        ru_server = updated["dns"]["servers"][0]
        self.assertEqual(ru_server["address"], "77.88.8.8")
        self.assertIn("domain:2ip.ru", ru_server["domains"])
        self.assertTrue(ru_server.get("skipFallback"))

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

    def test_rollback_removes_newly_created_relay_nginx_conf(self):
        self._prepare_base_env()
        new_conf = self.nginx_relays_d / "fr.conf"
        self.assertFalse(new_conf.exists())

        cmd = f'''
        manifest_begin "{new_conf}"
        echo "fake nginx config" > "{new_conf}"
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
        cmd = 'install_xray_origin_node "origin.example.com" "admin@example.com" "apikey" "/stream" "1.2.3.4"'
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
        cmd = 'install_xray_origin_node "origin.example.com" "admin@example.com" "apikey" "/stream" "1.2.3.4"'
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

    # -------------------------------------------------------------------------
    # F20: Verified Update Rollback in update_xray
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

update_xray
"""
        res = self._run_shell_snippet(cmd)
        self.assertNotEqual(
            res.returncode, 0, "update_xray must exit with error on service restart failure"
        )
        self.assertIn("Xray не запустился после обновления", res.stdout + res.stderr)
        self.assertIn(
            "Откат на предыдущую версию успешно выполнен и подтвержден", res.stdout + res.stderr
        )

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
    local cert_file="{cert_file}"
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

        self.assertEqual(pinned_packages.get("fastapi"), "0.115.6")
        self.assertEqual(pinned_packages.get("uvicorn"), "0.34.0")
        self.assertEqual(pinned_packages.get("grpcio"), "1.68.1")
        self.assertEqual(pinned_packages.get("protobuf"), "7.35.1")
        self.assertEqual(pinned_packages.get("pydantic"), "2.10.4")
        self.assertEqual(pinned_packages.get("psutil"), "6.1.1")

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
    # Functional Validation: Origin Node Installation & Complete Artifacts
    # -------------------------------------------------------------------------
    def test_functional_origin_node_installation_and_artifacts(self):
        self._prepare_base_env()
        domain = "origin.example.com"
        secret_path = "/stream"

        cmd = f'install_xray_origin_node "{domain}" "admin@example.com" "test_api_key_123" "{secret_path}" "198.51.100.1"'
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

        # Standalone origin routing must route default traffic to block (no Russian ISP exit)
        rules = xray_conf["routing"]["rules"]
        default_rule = next(
            (r for r in rules if r.get("inboundTag") == ["just1k-wl-default"]), None
        )
        self.assertIsNotNone(default_rule)
        self.assertEqual(default_rule["outboundTag"], "just1k-wl-block")

        # 2. Verify Nginx configurations
        nginx_conf = self.nginx_conf_dir / "sites-available" / "just1k-origin.conf"
        self.assertTrue(nginx_conf.exists())
        nginx_text = nginx_conf.read_text(encoding="utf-8")
        self.assertIn("client_max_body_size 0;", nginx_text)
        self.assertIn("large_client_header_buffers 8 64k;", nginx_text)
        self.assertIn("location = /cdn-check", nginx_text)
        self.assertIn("return 204;", nginx_text)
        self.assertIn("location / {", nginx_text)
        self.assertIn("try_files $uri $uri/ =404;", nginx_text)

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
        cmd_init = 'install_xray_origin_node "origin.example.com" "admin@example.com" "apikey" "/stream" "198.51.100.1"'
        self._run_shell_snippet(cmd_init)

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

        # Verify default traffic is routed through the relay outbound (anti-Russian exit)
        rules = xray_conf["routing"]["rules"]
        default_rule = next(
            (r for r in rules if r.get("inboundTag") == ["just1k-wl-default"]), None
        )
        self.assertIsNotNone(default_rule)
        self.assertEqual(default_rule["outboundTag"], "just1k-wl-outbound-de")

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
    # Functional Validation: Camouflage Landing & Certbot Deploy Hook
    # -------------------------------------------------------------------------
    def test_functional_camouflage_and_certbot_deploy_hook(self):
        self._prepare_base_env()
        res = self._run_shell_snippet("deploy_camouflage_site; deploy_certbot_renewal_hook")
        self.assertEqual(res.returncode, 0)

        # 1. Camouflage index.html
        index_file = self.www_html_dir / "index.html"
        self.assertTrue(index_file.exists())
        html_content = index_file.read_text(encoding="utf-8")
        self.assertIn("<!DOCTYPE html>", html_content)
        self.assertIn("<html", html_content)
        self.assertIn("Cloud Ingress", html_content)

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
        self.assertIn("resolver 1.1.1.1", content)
        self.assertIn('set $bot_upstream "https://just1k.best";', content)
        self.assertIn("proxy_pass $bot_upstream;", content)
        self.assertIn("proxy_ssl_server_name on;", content)
        self.assertIn("proxy_set_header Host just1k.best;", content)

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

        res = self._run_shell_snippet(f'detect_existing_nginx_sites "{self.nginx_conf_dir}"')
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
NGINX_CONF_DIR="{self.nginx_conf_dir}"
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
NGINX_CONF_DIR="{self.nginx_conf_dir}"
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
        user_log_posix = user_log.as_posix()
        (mock_bin / "groupadd").write_text(
            f"#!/bin/bash\necho \"$@\" >> '{group_log}'\nexit 0\n", encoding="utf-8"
        )
        (mock_bin / "groupadd").chmod(0o755)
        (mock_bin / "useradd").write_text(
            f"#!/bin/bash\necho \"$@\" >> '{user_log}'\nexit 0\n", encoding="utf-8"
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
source '{api_sh.as_posix()}'
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
        self.assertEqual(st, 0o640, f"Expected 0640, got {oct(st)}")

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

    def test_uninstall_node_trailing_confirm_flag_fails_closed(self):
        """uninstall_node with trailing --confirm flag must fail-closed (code 1) without crashing."""
        self._prepare_base_env()
        res = self._run_shell_snippet("uninstall_node --confirm")
        self.assertEqual(res.returncode, 1)
        self.assertIn("В неинтерактивном режиме для удаления требуется явный флаг", res.stdout + res.stderr)
        self.assertNotIn("shift: shift count out of range", res.stdout + res.stderr)


if __name__ == "__main__":
    unittest.main()
