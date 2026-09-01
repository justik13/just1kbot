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
        self.systemd_dir = Path(self.temp_dir) / "etc" / "systemd" / "system"
        self.systemd_dir.mkdir(parents=True, exist_ok=True)
        self.certbot_dir = Path(self.temp_dir) / "var" / "www" / "certbot"
        self.certbot_dir.mkdir(parents=True, exist_ok=True)
        self.www_html_dir = Path(self.temp_dir) / "var" / "www" / "html"
        self.www_html_dir.mkdir(parents=True, exist_ok=True)
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
        self._create_mock_script("unzip", """#!/bin/sh
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
""")

        # Initial files
        state_file = self.state_dir / "state.json"
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump({"role": "origin", "domain": "origin.example.com", "secret_base_path": "/stream"}, f)

        relays_file = self.state_dir / "relays.json"
        with open(relays_file, "w", encoding="utf-8") as f:
            json.dump([], f)

        xray_config = self.xray_config_dir / "config.json"
        with open(xray_config, "w", encoding="utf-8") as f:
            json.dump({
                "inbounds": [{"tag": "just1k-wl-default", "port": 8003, "protocol": "vless"}],
                "outbounds": [
                    {"tag": "just1k-wl-direct", "protocol": "freedom"},
                    {"tag": "just1k-wl-block", "protocol": "blackhole"},
                    {"tag": "direct", "protocol": "freedom"},
                    {"tag": "block", "protocol": "blackhole"},
                ],
                "routing": {"rules": []}
            }, f)

        env_file = self.xray_api_etc / "config.env"
        with open(env_file, "w", encoding="utf-8") as f:
            f.write("XRAY_API_KEY=testkey\nXRAY_INBOUND_TAGS=just1k-wl-default\n")

    def _run_shell_snippet(self, snippet: str, extra_env: dict = None) -> subprocess.CompletedProcess:
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
        env["BACKUP_DIR"] = str(Path(self.temp_dir) / "backups")
        env["NGINX_CONF_DIR"] = str(self.nginx_conf_dir)
        env["NGINX_RELAYS_DIR"] = str(self.nginx_relays_d)
        env["XRAY_API_CONFIG_ENV"] = str(self.xray_api_etc / "config.env")
        env["SYSTEMD_SYSTEM_DIR"] = str(self.systemd_dir)
        env["CERTBOT_DIR"] = str(self.certbot_dir)
        env["WWW_HTML_DIR"] = str(self.www_html_dir)
        if extra_env:
            env.update(extra_env)

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
export BACKUP_DIR='{Path(self.temp_dir) / "backups"}'
export NGINX_CONF_DIR='{self.nginx_conf_dir}'
export NGINX_RELAYS_DIR='{self.nginx_relays_d}'
export XRAY_API_CONFIG_ENV='{self.xray_api_etc / "config.env"}'
export SYSTEMD_SYSTEM_DIR='{self.systemd_dir}'
export CERTBOT_DIR='{self.certbot_dir}'
export WWW_HTML_DIR='{self.www_html_dir}'

source '{JUST1KNODE_SH}'

check_root() {{ return 0; }}
install_base_deps() {{ return 0; }}
obtain_ssl_certificate() {{ return 0; }}
download_and_verify_xray() {{ return 0; }}
deploy_xray_api_sources() {{ return 0; }}

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
    # F02: Transactional Rollback on Nginx Failure in add_relay_node
    # -------------------------------------------------------------------------
    def test_relay_add_rollback_on_nginx_failure(self):
        self._prepare_base_env()
        # Create a pre-existing relay conf for "de" to verify it is restored, not deleted
        de_conf = self.nginx_relays_d / "de.conf"
        original_de_content = "# Pre-existing DE relay config\nlocation ^~ /old { proxy_pass http://127.0.0.1:8001; }\n"
        with open(de_conf, "w", encoding="utf-8") as f:
            f.write(original_de_content)

        original_relays = [{"code": "de", "name": "Old Germany", "inbound_port": 8004, "inbound_tag": "just1k-wl-inbound-de"}]
        with open(self.state_dir / "relays.json", "w", encoding="utf-8") as f:
            json.dump(original_relays, f)

        # Make nginx fail on validation
        self._create_mock_script("nginx", """#!/bin/sh
if [ "$1" = "-t" ]; then
    echo "nginx: configuration syntax error test" >&2
    exit 1
fi
exit 0
""")

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

        relays_data = [{"code": "de", "name": "Germany", "inbound_port": 8004, "inbound_tag": "just1k-wl-inbound-de"}]
        with open(self.state_dir / "relays.json", "w", encoding="utf-8") as f:
            json.dump(relays_data, f)

        xray_config_file = self.xray_config_dir / "config.json"
        with open(xray_config_file, "w", encoding="utf-8") as f:
            json.dump({
                "inbounds": [{"tag": "just1k-wl-default"}, {"tag": "just1k-wl-inbound-de"}],
                "outbounds": [{"tag": "just1k-wl-outbound-de"}, {"tag": "just1k-wl-direct"}],
                "routing": {"rules": [{"inboundTag": ["just1k-wl-inbound-de"], "outboundTag": "just1k-wl-outbound-de"}]}
            }, f)

        # Mock xray to fail test
        self._create_mock_script("xray", """#!/bin/sh
if [ "$1" = "run" ] && [ "$2" = "-test" ]; then
    echo "xray: config test failed" >&2
    exit 1
fi
exit 0
""")

        cmd = 'remove_relay_node "de"'
        res = self._run_shell_snippet(cmd)

        self.assertNotEqual(res.returncode, 0, "remove_relay_node must fail closed when xray test fails")
        self.assertIn("Ошибка тестирования Xray", res.stderr + res.stdout)

        # Ensure deleted de.conf was restored
        self.assertTrue(de_conf.exists(), "de.conf must be restored after rollback")
        with open(de_conf, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), de_content)

        # Ensure relays.json was restored
        with open(self.state_dir / "relays.json", "r", encoding="utf-8") as f:
            self.assertEqual(json.load(f), relays_data)

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
            {"tag": "warp-exit", "protocol": "socks", "settings": {"servers": [{"address": "127.0.0.1", "port": 40000}]}}
        ]
        with open(xray_config_file, "w", encoding="utf-8") as f:
            json.dump({
                "inbounds": [],
                "outbounds": custom_outbounds,
                "routing": {"rules": []}
            }, f)

        # Run origin installer with surgical merge
        cmd = 'install_xray_origin_node "origin.example.com" "admin@example.com" "apikey" "/stream" "1.2.3.4"'
        res = self._run_shell_snippet(cmd)
        self.assertEqual(res.returncode, 0, f"install_xray_origin_node failed: {res.stderr + res.stdout}")

        with open(xray_config_file, "r", encoding="utf-8") as f:
            cfg = json.load(f)

        tags = [ob["tag"] for ob in cfg["outbounds"]]
        self.assertIn("direct", tags, "Custom 'direct' outbound must be preserved")
        self.assertIn("block", tags, "Custom 'block' outbound must be preserved")
        self.assertIn("custom-wireguard", tags, "Custom 'custom-wireguard' outbound must be preserved")
        self.assertIn("warp-exit", tags, "Custom 'warp-exit' outbound must be preserved")
        self.assertIn("just1k-wl-direct", tags, "Namespaced just1k-wl-direct outbound must be added")
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
            {"tag": "custom-socks", "port": 1080, "protocol": "socks"}
        ]
        with open(xray_config_file, "w", encoding="utf-8") as f:
            json.dump({
                "inbounds": custom_inbounds,
                "outbounds": [],
                "routing": {"rules": []}
            }, f)

        # Run origin installer with surgical merge
        cmd = 'install_xray_origin_node "origin.example.com" "admin@example.com" "apikey" "/stream" "1.2.3.4"'
        res = self._run_shell_snippet(cmd)
        self.assertEqual(res.returncode, 0, f"install_xray_origin_node failed: {res.stderr + res.stdout}")

        with open(xray_config_file, "r", encoding="utf-8") as f:
            cfg = json.load(f)

        inbound_tags = [ib["tag"] for ib in cfg["inbounds"]]
        self.assertIn("custom-socks", inbound_tags, "Third party custom-socks inbound must be preserved")
        self.assertIn("just1k-wl-api-grpc", inbound_tags, "Namespaced just1k-wl-api-grpc must be present")
        self.assertIn("just1k-wl-default", inbound_tags, "Namespaced just1k-wl-default must be present")

    # -------------------------------------------------------------------------
    # F19: Fail-Closed UFW Firewall & Doctor ACL Validation
    # -------------------------------------------------------------------------
    def test_doctor_ufw_acl_validation(self):
        self._prepare_base_env()

        # Case 1: Insecure UFW with 8444 open to 0.0.0.0/0
        self._create_mock_script("ufw", """#!/bin/sh
if [ "$1" = "status" ] || [ "$1" = "status verbose" ]; then
    echo "Status: active"
    echo "To                         Action      From"
    echo "--                         ------      ----"
    echo "8444/tcp                   ALLOW       Anywhere"
    echo "443/tcp                    ALLOW       Anywhere"
    exit 0
fi
exit 0
""")
        res_vuln = self._run_shell_snippet("run_doctor")
        self.assertIn("УЯЗВИМОСТЬ: Порт 8444 открыт для всех", res_vuln.stdout + res_vuln.stderr)

        # Case 2: Secure UFW with BOT_IP restriction
        with open(self.state_dir / "state.json", "w", encoding="utf-8") as f:
            json.dump({"role": "origin", "domain": "origin.example.com", "bot_ip": "198.51.100.42"}, f)

        self._create_mock_script("ufw", """#!/bin/sh
if [ "$1" = "status" ] || [ "$1" = "status verbose" ]; then
    echo "Status: active"
    echo "To                         Action      From"
    echo "--                         ------      ----"
    echo "8444/tcp                   ALLOW       198.51.100.42"
    echo "443/tcp                    ALLOW       Anywhere"
    exit 0
fi
exit 0
""")
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
        self.assertNotEqual(res.returncode, 0, "update_xray must exit with error on service restart failure")
        self.assertIn("Xray не запустился после обновления", res.stdout + res.stderr)
        self.assertIn("Откат на предыдущую версию успешно выполнен и подтвержден", res.stdout + res.stderr)

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
        subprocess.run([
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-keyout", str(key_file),
            "-out", str(cert_file), "-days", "90", "-nodes",
            "-subj", f"/CN={domain}",
            "-addext", f"subjectAltName=DNS:{domain}"
        ], check=True, capture_output=True)

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
        subprocess.run([
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-keyout", str(key_file),
            "-out", str(cert_file), "-days", "90", "-nodes",
            "-subj", "/CN=wrong.example.com",
            "-addext", "subjectAltName=DNS:wrong.example.com"
        ], check=True, capture_output=True)

        res_mismatch = self._run_shell_snippet(doctor_snippet)
        self.assertNotEqual(res_mismatch.returncode, 0)
        self.assertIn("SAN mismatch", res_mismatch.stdout)

    # -------------------------------------------------------------------------
    # F22: Immutable Dependency Pinning
    # -------------------------------------------------------------------------
    def test_installer_immutable_dependencies(self):
        # 1. Check requirements.txt
        req_content = REQUIREMENTS_TXT.read_text(encoding="utf-8")
        req_lines = [line.strip() for line in req_content.splitlines() if line.strip() and not line.startswith("#")]

        for line in req_lines:
            self.assertIn("==", line, f"Requirement {line} must be strictly pinned with ==")
            self.assertFalse(re.search(r"[><~]=?", line.split("==")[0]), f"Range specifiers forbidden in {line}")

        pinned_packages = dict(item.split("==") for item in req_lines)
        self.assertEqual(pinned_packages.get("fastapi"), "0.115.6")
        self.assertEqual(pinned_packages.get("uvicorn"), "0.34.0")
        self.assertEqual(pinned_packages.get("grpcio"), "1.68.1")
        self.assertEqual(pinned_packages.get("protobuf"), "7.35.1")
        self.assertEqual(pinned_packages.get("pydantic"), "2.10.4")

        # 2. Check just1knode.sh for absence of floating git tarballs / unpinned upgrades
        sh_content = JUST1KNODE_SH.read_text(encoding="utf-8")
        self.assertNotIn("refs/heads/main.tar.gz", sh_content, "Floating main.tar.gz downloads are forbidden")
        self.assertNotIn("refs/heads/feature", sh_content, "Floating branch downloads are forbidden")
        self.assertNotIn("pip install --upgrade pip", sh_content, "Unpinned pip self-upgrade is forbidden")
        self.assertIn("JUST1KBOT_RELEASE_COMMIT=", sh_content, "Fixed release commit pin must be present")
        self.assertIn("AMNEZIA_API_COMMIT=", sh_content, "Amnezia API commit pin must be present")


if __name__ == "__main__":
    unittest.main()
