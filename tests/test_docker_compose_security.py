import ipaddress
import re
import unittest
from pathlib import Path

from config.constants import YOOKASSA_IP_RANGES


class DockerComposeSecurityTests(unittest.TestCase):
    def test_database_and_redis_are_not_published_on_host(self):
        compose = (Path(__file__).parents[1] / "docker-compose.yml").read_text(
            encoding="utf-8"
        )
        self.assertNotIn('"5432:5432"', compose)
        self.assertNotIn('"6379:6379"', compose)

    def test_public_compose_ports_are_limited_to_caddy(self):
        compose = (Path(__file__).parents[1] / "docker-compose.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn('"80:80"', compose)
        self.assertIn('"443:443"', compose)

    def test_caddy_config_does_not_require_custom_plugins(self):
        root = Path(__file__).parents[1]
        caddyfile = (root / "Caddyfile").read_text(encoding="utf-8")
        caddyfile_ci = (root / "Caddyfile.ci").read_text(encoding="utf-8")
        dockerfile = (root / "Dockerfile.caddy").read_text(encoding="utf-8")

        self.assertNotIn("order rate_limit", caddyfile)
        self.assertNotIn("rate_limit {", caddyfile)
        self.assertNotIn("order rate_limit", caddyfile_ci)
        self.assertNotIn("rate_limit {", caddyfile_ci)
        self.assertNotIn("xcaddy", dockerfile)

    def test_compose_network_segmentation_and_resource_bounds(self):
        compose = (Path(__file__).parents[1] / "docker-compose.yml").read_text(
            encoding="utf-8"
        )

        # 1. Networks defined
        self.assertIn("frontend_net:", compose)
        self.assertIn("backend_net:", compose)

        # 2. Service network assignments
        self.assertTrue(re.search(r"db:.*?networks:\s*-\s*backend_net", compose, re.DOTALL))
        self.assertTrue(re.search(r"redis:.*?networks:\s*-\s*backend_net", compose, re.DOTALL))
        self.assertTrue(re.search(r"migrate:.*?networks:\s*-\s*backend_net", compose, re.DOTALL))
        self.assertTrue(re.search(r"backup:.*?networks:\s*-\s*backend_net", compose, re.DOTALL))
        self.assertTrue(re.search(r"caddy:.*?networks:\s*-\s*frontend_net", compose, re.DOTALL))
        self.assertTrue(re.search(r"bot:.*?networks:\s*-\s*frontend_net\s*-\s*backend_net", compose, re.DOTALL))

        # 3. Redis bounds
        self.assertIn("--maxmemory 384mb", compose)
        self.assertIn("--maxmemory-policy noeviction", compose)

        # 4. Caddy ports
        self.assertIn('"80:80"', compose)
        self.assertIn('"443:443"', compose)

        # 5. Postgres and Bot graceful shutdown
        self.assertIn("stop_grace_period: 30s", compose)
        self.assertIn('shm_size: "256m"', compose)

    def test_caddy_ingress_routes_reject_unmatched_and_restrict_backend_proxy(self):
        root = Path(__file__).parents[1]
        expected_yookassa_networks = {
            ipaddress.ip_network(cidr, strict=False) for cidr in YOOKASSA_IP_RANGES
        }
        for fname in ("Caddyfile", "Caddyfile.ci"):
            content = (root / fname).read_text(encoding="utf-8")
            self.assertIn("@yookassa_allowed", content)

            # SSOT Check: ensure Caddyfile remote_ip ranges match config.constants.YOOKASSA_IP_RANGES exactly
            match = re.search(r"remote_ip\s+([0-9a-fA-F:\./ ]+)", content)
            self.assertIsNotNone(match, f"remote_ip directive not found in {fname}")
            raw_ips = match.group(1).split()
            caddy_networks = {ipaddress.ip_network(ip, strict=False) for ip in raw_ips}
            self.assertEqual(
                caddy_networks,
                expected_yookassa_networks,
                f"Mismatch between {fname} and config.constants.YOOKASSA_IP_RANGES",
            )
            self.assertIn(
                "{$YOOKASSA_EXTRA_IPS:127.0.0.1/32}",
                content,
                f"Missing YOOKASSA_EXTRA_IPS fallback token in {fname}",
            )

            self.assertIn("@subscription_paths path", content)
            self.assertNotIn("@limited_body_paths path /health", content)
            self.assertNotIn("path /health", content)
            self.assertIn('respond "Not Found" 404', content)
            # Ensure no catch-all reverse_proxy block exists
            self.assertNotIn("handle {\n\t\treverse_proxy bot:8080", content)
            self.assertNotIn("handle {\n        reverse_proxy bot:8080", content)

    def test_scripts_contain_redis_overcommit_configuration_and_doctor_check(self):
        root = Path(__file__).parents[1]
        setup_sh = (root / "scripts" / "setup.sh").read_text(encoding="utf-8")
        cli_sh = (root / "scripts" / "cli.sh").read_text(encoding="utf-8")
        self.assertIn("vm.overcommit_memory", setup_sh)
        self.assertIn("vm.overcommit_memory=1", setup_sh)
        self.assertIn("vm.overcommit_memory", cli_sh)

    def test_caddy_stealth_sni_and_direct_ip_abort(self):
        root = Path(__file__).parents[1]
        caddyfile = (root / "Caddyfile").read_text(encoding="utf-8")
        self.assertIn("strict_sni_host", caddyfile)
        self.assertIn("http:// {", caddyfile)
        self.assertIn("abort", caddyfile)

    def test_caddy_modular_conf_d_and_uptime_kuma_integration(self):
        root = Path(__file__).parents[1]
        caddyfile = (root / "Caddyfile").read_text(encoding="utf-8")
        compose = (root / "docker-compose.yml").read_text(encoding="utf-8")
        cli_sh = (root / "scripts" / "cli.sh").read_text(encoding="utf-8")
        example_caddy = (root / "caddy_conf.d" / "status.caddy.example").read_text(encoding="utf-8")

        # 1. Caddyfile modular import
        self.assertIn("import conf.d/*.caddy", caddyfile)

        # 2. docker-compose mounts caddy_conf.d into /etc/caddy/conf.d and isolates status_net
        self.assertIn("./caddy_conf.d:/etc/caddy/conf.d:ro", compose)
        self.assertIn("status_net:", compose)

        # 3. scripts/cli.sh connects uptime-kuma to isolated status_net only when status.caddy is present
        self.assertIn("ensure_status_ingress_network", cli_sh)
        self.assertIn("caddy_conf.d/status.caddy", cli_sh)
        self.assertIn("status_net", cli_sh)
        self.assertNotIn("docker network connect just1kbot_frontend_net uptime-kuma", cli_sh)

        # 4. Uninstall cleans up status_net safely disconnecting endpoints
        self.assertIn("${project_basename}_status_net", cli_sh)
        self.assertIn("just1kbot_status_net", cli_sh)
        self.assertIn("docker network disconnect -f", cli_sh)

        # 5. Example configuration contains security headers and upstream
        self.assertIn("reverse_proxy uptime-kuma:3001", example_caddy)
        self.assertIn("-Server", example_caddy)
        self.assertIn('X-Robots-Tag "noindex, nofollow, noarchive"', example_caddy)
        self.assertIn('Permissions-Policy "camera=(), microphone=(), geolocation=()"', example_caddy)

        # 6. Update applies Caddy configuration via zero-downtime safe reload
        self.assertIn("caddy reload --config /etc/caddy/Caddyfile", cli_sh)
        self.assertIn("Не удалось применить новую конфигурацию Caddy", cli_sh)

    def test_bot_firewall_and_stealth_ssot_invariants(self):
        root = Path(__file__).parents[1]
        cli_sh = (root / "scripts" / "cli.sh").read_text(encoding="utf-8")
        setup_sh = (root / "scripts" / "setup.sh").read_text(encoding="utf-8")

        # cli.sh and setup.sh include IPv6 leak protection sysctl and safe caddy reload
        self.assertIn("net.ipv6.conf.all.disable_ipv6 = 1", cli_sh)
        self.assertIn("net.ipv6.conf.all.disable_ipv6 = 1", setup_sh)
        self.assertIn("caddy reload --config /etc/caddy/Caddyfile", cli_sh)
        self.assertNotIn("caddy reload --config /etc/caddy/Caddyfile 2>/dev/null || docker restart", cli_sh)

    def test_backup_service_google_drive_configuration_and_isolation(self):
        root = Path(__file__).parents[1]
        compose = (root / "docker-compose.yml").read_text(encoding="utf-8")
        dockerfile_backup = (root / "Dockerfile.backup").read_text(encoding="utf-8")
        backup_sh = (root / "scripts" / "docker" / "backup.sh").read_text(encoding="utf-8")
        cli_sh = (root / "scripts" / "cli.sh").read_text(encoding="utf-8")
        env_example = (root / ".env.example").read_text(encoding="utf-8")
        gitignore = (root / ".gitignore").read_text(encoding="utf-8")

        # 1. Dockerfile.backup includes rclone
        self.assertIn("rclone", dockerfile_backup)

        # 2. docker-compose passes GDRIVE_BACKUP_ENABLED and GDRIVE_RETENTION_DAYS,
        # but NEVER passes OAuth tokens or credentials in environment (they live exclusively in backups/rclone.conf)
        self.assertIn("GDRIVE_BACKUP_ENABLED:", compose)
        self.assertIn("GDRIVE_RETENTION_DAYS:", compose)
        self.assertNotIn("GDRIVE_TOKEN_BASE64", compose)
        self.assertNotIn("GDRIVE_FOLDER_ID", compose)
        self.assertNotIn("GDRIVE_SA_BASE64", compose)
        self.assertNotIn("GDRIVE_SA_FILE", compose)

        # 3. scripts/docker/backup.sh reads canonical /backups/rclone.conf, enforces fail-closed, mandatory folder, and scoped retention
        self.assertIn('RCLONE_CONF="/backups/rclone.conf"', backup_sh)
        self.assertIn('rclone --config "$RCLONE_CONF" copy', backup_sh)
        self.assertIn('rclone --config "$RCLONE_CONF" delete --include "just1kbot_*.sql.gz.age"', backup_sh)
        self.assertIn('chmod 600 "$RCLONE_CONF"', backup_sh)
        self.assertIn('root_folder_id', backup_sh)
        self.assertIn('exit 1', backup_sh)
        self.assertIn('if [[ "$GDRIVE_ENABLED" == "true" ]]; then', backup_sh)

        # 4. scripts/cli.sh rebuilds tools profile during update, checks Google Drive in doctor, has interactive wizard, and protects uninstalled backups
        self.assertIn("docker compose --profile tools build backup", cli_sh)
        self.assertIn("GDRIVE_BACKUP_ENABLED", cli_sh)
        self.assertIn("Google Drive бэкап", cli_sh)
        self.assertIn("cmd_setup_gdrive", cli_sh)
        self.assertIn("gdrive|setup-gdrive)", cli_sh)
        self.assertIn("backups/rclone.conf", cli_sh)
        self.assertIn("safe_backup_dest", cli_sh)
        self.assertIn("--entrypoint rclone", cli_sh)
        self.assertIn("sys.stdin", cli_sh)
        self.assertIn("Фаервол UFW активен", cli_sh)
        self.assertIn("«Мёртвое» правило в UFW", cli_sh)
        self.assertIn("ВНИМАНИЕ: Посторонний порт", cli_sh)
        self.assertIn("warned_dead_targets", cli_sh)
        self.assertIn("warned_public_targets", cli_sh)
        self.assertIn("LC_ALL=C ufw status verbose", cli_sh)

        # 5. .env.example documents only non-secret Google Drive flags (no tokens)
        self.assertIn("GDRIVE_BACKUP_ENABLED=false", env_example)
        self.assertIn("GDRIVE_RETENTION_DAYS=14", env_example)
        self.assertNotIn("GDRIVE_TOKEN_BASE64", env_example)

        # 6. .gitignore protects credentials and rclone.conf
        self.assertIn("*gdrive*.json", gitignore)
        self.assertIn("*service_account*.json", gitignore)
        self.assertIn("*rclone*.conf", gitignore)


