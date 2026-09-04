"""Behavioural test suite for scripts/cli.sh and scripts/setup.sh deployment logic.

Tests execute shell scenarios in isolated temporary directories:
- Pre-flight checks: missing env, strict 600 permissions, duplicate keys, required vars, format validation.
- Safe Git synchronization: fast-forward, unpushed local changes (backup-local-ahead-*), diverged history (backup-diverged-*), stash handling.
- Post-migration and healthcheck failure handling: rollback commit verification and restore command generation.
- Apt lock timeout failure: wait_for_apt_locks timeout return code.
- Restore safety: confirmation prompt validation and backup file existence.
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

CLI_PATH = Path(__file__).resolve().parent.parent / "scripts" / "cli.sh"
SETUP_PATH = Path(__file__).resolve().parent.parent / "scripts" / "setup.sh"


@unittest.skipUnless(shutil.which("bash"), "Bash is required for shell behavioural tests")
class CliBehaviouralTests(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.test_dir.name)
        self.project_dir = self.root / "just1kbot"
        self.project_dir.mkdir(parents=True, exist_ok=True)

        # Copy cli.sh and setup.sh to project dir
        scripts_dir = self.project_dir / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(CLI_PATH, scripts_dir / "cli.sh")
        shutil.copy(SETUP_PATH, scripts_dir / "setup.sh")
        (scripts_dir / "cli.sh").chmod(0o755)
        (scripts_dir / "setup.sh").chmod(0o755)

        # Create dummy docker-compose.yml so cli.sh recognizes PROJECT_DIR
        (self.project_dir / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")

        # Create bin dir with docker stub for preflight docker daemon checks in test environment
        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir(parents=True, exist_ok=True)
        docker_stub = self.bin_dir / "docker"
        docker_stub.write_text(
            '#!/bin/bash\n'
            'if [[ "$1" == "info" ]]; then exit 0; fi\n'
            'if [[ "$1" == "compose" && "$2" == "version" ]]; then echo "Docker Compose version v2.27.0"; exit 0; fi\n'
            'exit 1\n',
            encoding="utf-8",
        )
        docker_stub.chmod(0o755)

    def tearDown(self):
        self.test_dir.cleanup()

    def _run_cli_command(self, *args: str, env_vars: dict[str, str] | None = None, input_text: str | None = None) -> subprocess.CompletedProcess:
        """Run cli.sh directly with args inside isolated test project directory."""
        proc_env = os.environ.copy()
        proc_env["PROJECT_DIR"] = self.project_dir.as_posix()
        proc_env["JUST1KBOT_DIR"] = self.project_dir.as_posix()
        proc_env["PATH"] = f"{self.bin_dir.as_posix()}:{proc_env.get('PATH', '')}"
        if env_vars:
            proc_env.update(env_vars)

        return subprocess.run(
            ["bash", (self.project_dir / "scripts" / "cli.sh").as_posix(), *args],
            cwd=str(self.project_dir),
            input=input_text,
            capture_output=True,
            text=True,
            env=proc_env,
            check=False,
        )

    # -------------------------------------------------------------------------
    # 1. Preflight Validation Behavioural Tests
    # -------------------------------------------------------------------------

    def test_preflight_fails_when_env_file_missing(self):
        """cmd_preflight returns exit code 1 when .env is absent."""
        proc = self._run_cli_command("preflight")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("не найден", proc.stdout + proc.stderr)

    def test_preflight_fails_when_duplicate_keys_present(self):
        """cmd_preflight returns exit code 1 when .env contains duplicate keys."""
        env_content = (
            "BOT_TOKEN=token123\n"
            "BOT_TOKEN=token456\n"
            "POSTGRES_USER=user\n"
            "POSTGRES_PASSWORD=pass\n"
            "POSTGRES_DB=db\n"
            "DB_ENCRYPTION_KEY=key\n"
            "BACKUP_AGE_RECIPIENT=age1test\n"
            "ADMIN_IDS=[123]\n"
        )
        (self.project_dir / ".env").write_text(env_content, encoding="utf-8")
        (self.project_dir / ".env").chmod(0o600)

        proc = self._run_cli_command("preflight")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("дублирующиеся", proc.stdout + proc.stderr)

    def test_preflight_fails_when_required_vars_missing(self):
        """cmd_preflight fails if required keys are missing or empty."""
        env_content = (
            "BOT_TOKEN=\n"
            "POSTGRES_USER=user\n"
        )
        (self.project_dir / ".env").write_text(env_content, encoding="utf-8")
        (self.project_dir / ".env").chmod(0o600)

        proc = self._run_cli_command("preflight")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("отсутствует или пуста обязательная переменная: BOT_TOKEN", proc.stdout + proc.stderr)

    def test_preflight_fails_when_ssl_email_malformed(self):
        """cmd_preflight rejects invalid email format in SSL_EMAIL."""
        env_content = (
            "BOT_TOKEN=token123\n"
            "POSTGRES_USER=user\n"
            "POSTGRES_PASSWORD=pass\n"
            "POSTGRES_DB=db\n"
            "DB_ENCRYPTION_KEY=key\n"
            "BACKUP_AGE_RECIPIENT=age1test\n"
            "ADMIN_IDS=[123]\n"
            "SSL_EMAIL=invalid-email-format\n"
        )
        (self.project_dir / ".env").write_text(env_content, encoding="utf-8")
        (self.project_dir / ".env").chmod(0o600)

        proc = self._run_cli_command("preflight")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("Некорректный email", proc.stdout + proc.stderr)

    def test_preflight_fails_when_domain_has_protocol(self):
        """cmd_preflight rejects DOMAIN if prefixed with http:// or https://."""
        env_content = (
            "BOT_TOKEN=token123\n"
            "POSTGRES_USER=user\n"
            "POSTGRES_PASSWORD=pass\n"
            "POSTGRES_DB=db\n"
            "DB_ENCRYPTION_KEY=key\n"
            "BACKUP_AGE_RECIPIENT=age1test\n"
            "ADMIN_IDS=[123]\n"
            "DOMAIN=https://vpn.example.com\n"
        )
        (self.project_dir / ".env").write_text(env_content, encoding="utf-8")
        (self.project_dir / ".env").chmod(0o600)

        proc = self._run_cli_command("preflight")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("DOMAIN не должен содержать протокол", proc.stdout + proc.stderr)

    # -------------------------------------------------------------------------
    # 2. Git Synchronization State Machine Tests
    # -------------------------------------------------------------------------

    def _init_git_scenario(self) -> Path:
        """Create an upstream bare repository and clone it to simulate production update."""
        if not shutil.which("git"):
            self.skipTest("git is not installed in test environment")

        upstream_dir = self.root / "upstream.git"
        subprocess.run(["git", "init", "--bare", str(upstream_dir)], check=True, capture_output=True)

        # Clone repo
        work_dir = self.root / "work"
        subprocess.run(["git", "clone", str(upstream_dir), str(work_dir)], check=True, capture_output=True)

        subprocess.run(["git", "config", "user.email", "audit@test.local"], cwd=work_dir, check=True)
        subprocess.run(["git", "config", "user.name", "Audit Runner"], cwd=work_dir, check=True)

        (work_dir / "README.md").write_text("# Initial", encoding="utf-8")
        subprocess.run(["git", "checkout", "-B", "main"], cwd=work_dir, check=True)
        subprocess.run(["git", "add", "README.md"], cwd=work_dir, check=True)
        subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=work_dir, check=True)
        subprocess.run(["git", "push", "-u", "origin", "main"], cwd=work_dir, check=True)

        return work_dir

    def _setup_git_work_dir_project(self, work_dir: Path):
        """Prepare work_dir with required project files so production cmd_update can execute."""
        scripts_dir = work_dir / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(CLI_PATH, scripts_dir / "cli.sh")
        (scripts_dir / "cli.sh").chmod(0o755)
        (work_dir / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
        (work_dir / ".gitignore").write_text(".env\nbackups/\n*.age\n", encoding="utf-8")
        env_content = (
            "BOT_TOKEN=token123\n"
            "POSTGRES_USER=user\n"
            "POSTGRES_PASSWORD=pass\n"
            "POSTGRES_DB=db\n"
            "DB_ENCRYPTION_KEY=key\n"
            "BACKUP_AGE_RECIPIENT=age1test\n"
            "ADMIN_IDS=[123]\n"
            "DOMAIN=vpn.example.com\n"
            "SSL_EMAIL=admin@example.com\n"
            "SUPPORT_USERNAME=support\n"
            "YOOKASSA_SHOP_ID=123\n"
            "YOOKASSA_SECRET_KEY=sec\n"
        )
        (work_dir / ".env").write_text(env_content, encoding="utf-8")
        (work_dir / ".env").chmod(0o600)
        subprocess.run(["git", "add", "scripts", "docker-compose.yml", ".gitignore"], cwd=work_dir, check=True)
        subprocess.run(["git", "commit", "-m", "Add project files"], cwd=work_dir, check=True)
        subprocess.run(["git", "push", "origin", "main"], cwd=work_dir, check=True)

    def test_git_sync_local_ahead_creates_backup_branch(self):
        """When local branch has unpushed commits ahead of upstream, production cmd_update creates a backup branch."""
        work_dir = self._init_git_scenario()
        self._setup_git_work_dir_project(work_dir)

        # Add local unpushed commit
        (work_dir / "local_change.txt").write_text("local only", encoding="utf-8")
        subprocess.run(["git", "add", "local_change.txt"], cwd=work_dir, check=True)
        subprocess.run(["git", "commit", "-m", "Unpushed commit"], cwd=work_dir, check=True)

        # Run real production cmd_update function
        test_script = f"""
export PROJECT_DIR="{work_dir.as_posix()}"
export JUST1KBOT_DIR="{work_dir.as_posix()}"
export PATH="{self.bin_dir.as_posix()}:$PATH"
cd "{work_dir.as_posix()}"
source scripts/cli.sh >/dev/null 2>&1 || true

# Mock cmd_backup so step 2 passes and update reaches step 3
cmd_backup() {{
    LAST_BACKUP_FILE="{work_dir.as_posix()}/dummy.sql.gz.age"
    touch "$LAST_BACKUP_FILE"
    return 0
}}

cmd_update
"""
        proc = subprocess.run(["bash", "-c", test_script], cwd=str(work_dir), input="y\n", capture_output=True, text=True, check=False)
        self.assertIn("Локальные коммиты сохранены в резервной ветке: backup-local-ahead-", proc.stdout + proc.stderr)

        # Verify branch exists
        branches = subprocess.run(["git", "branch"], cwd=work_dir, capture_output=True, text=True, check=True).stdout
        self.assertIn("backup-local-ahead-", branches)

    def test_git_sync_diverged_creates_backup_branch(self):
        """When local and upstream have diverged, production cmd_update creates a backup-diverged branch."""
        work_dir = self._init_git_scenario()
        self._setup_git_work_dir_project(work_dir)

        # Clone another working copy to push upstream change
        other_dir = self.root / "other"
        upstream_dir = self.root / "upstream.git"
        subprocess.run(["git", "clone", "-b", "main", str(upstream_dir), str(other_dir)], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "audit2@test.local"], cwd=other_dir, check=True)
        subprocess.run(["git", "config", "user.name", "Audit Runner 2"], cwd=other_dir, check=True)

        (other_dir / "remote_change.txt").write_text("remote change", encoding="utf-8")
        subprocess.run(["git", "add", "remote_change.txt"], cwd=other_dir, check=True)
        subprocess.run(["git", "commit", "-m", "Remote change"], cwd=other_dir, check=True)
        subprocess.run(["git", "push", "origin", "main"], cwd=other_dir, check=True)

        # In work_dir, make a conflicting local commit
        (work_dir / "diverged_local.txt").write_text("diverged local", encoding="utf-8")
        subprocess.run(["git", "add", "diverged_local.txt"], cwd=work_dir, check=True)
        subprocess.run(["git", "commit", "-m", "Local diverged commit"], cwd=work_dir, check=True)

        # Run real production cmd_update function
        test_script = f"""
export PROJECT_DIR="{work_dir.as_posix()}"
export JUST1KBOT_DIR="{work_dir.as_posix()}"
export PATH="{self.bin_dir.as_posix()}:$PATH"
cd "{work_dir.as_posix()}"
source scripts/cli.sh >/dev/null 2>&1 || true

# Mock cmd_backup so step 2 passes and update reaches step 3
cmd_backup() {{
    LAST_BACKUP_FILE="{work_dir.as_posix()}/dummy.sql.gz.age"
    touch "$LAST_BACKUP_FILE"
    return 0
}}

cmd_update
"""
        proc = subprocess.run(["bash", "-c", test_script], cwd=str(work_dir), input="y\n", capture_output=True, text=True, check=False)
        self.assertIn("Локальная история сохранена в ветке backup-diverged-", proc.stdout + proc.stderr)

        # Verify branch exists
        branches = subprocess.run(["git", "branch"], cwd=work_dir, capture_output=True, text=True, check=True).stdout
        self.assertIn("backup-diverged-", branches)

    def test_preflight_fails_when_admin_ids_non_numeric(self):
        """cmd_preflight rejects ADMIN_IDS if non-numeric, negative, or malformed."""
        for bad_val in ["[abc]", "[-1]", "[123,,456]"]:
            with self.subTest(bad_val=bad_val):
                env_content = (
                    "BOT_TOKEN=token123\n"
                    "POSTGRES_USER=user\n"
                    "POSTGRES_PASSWORD=pass\n"
                    "POSTGRES_DB=db\n"
                    "DB_ENCRYPTION_KEY=key\n"
                    "BACKUP_AGE_RECIPIENT=age1test\n"
                    f"ADMIN_IDS={bad_val}\n"
                    "DOMAIN=vpn.example.com\n"
                    "SSL_EMAIL=admin@example.com\n"
                    "SUPPORT_USERNAME=support\n"
                    "YOOKASSA_SHOP_ID=123\n"
                    "YOOKASSA_SECRET_KEY=sec\n"
                )
                (self.project_dir / ".env").write_text(env_content, encoding="utf-8")
                (self.project_dir / ".env").chmod(0o600)

                proc = self._run_cli_command("preflight")
                self.assertEqual(proc.returncode, 1)
                self.assertIn("Некорректный ID администратора", proc.stdout + proc.stderr)

    def test_update_aborts_immediately_when_preflight_fails(self):
        """cmd_update fails closed and halts immediately if preflight fails."""
        proc = self._run_cli_command("update")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("предварительная проверка не пройдена", proc.stdout + proc.stderr)
        self.assertNotIn("Шаг 2/6", proc.stdout + proc.stderr)

    def test_update_aborts_when_backup_fails(self):
        """cmd_update fails closed and halts if backup cannot be created."""
        env_content = (
            "BOT_TOKEN=token123\n"
            "POSTGRES_USER=user\n"
            "POSTGRES_PASSWORD=pass\n"
            "POSTGRES_DB=db\n"
            "DB_ENCRYPTION_KEY=key\n"
            "BACKUP_AGE_RECIPIENT=age1test\n"
            "ADMIN_IDS=[123]\n"
            "DOMAIN=vpn.example.com\n"
            "SSL_EMAIL=admin@example.com\n"
            "SUPPORT_USERNAME=support\n"
            "YOOKASSA_SHOP_ID=123\n"
            "YOOKASSA_SECRET_KEY=sec\n"
        )
        (self.project_dir / ".env").write_text(env_content, encoding="utf-8")
        (self.project_dir / ".env").chmod(0o600)

        proc = self._run_cli_command("update")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("не удалось создать страховочный бэкап", proc.stdout + proc.stderr)
        self.assertNotIn("Шаг 3/6", proc.stdout + proc.stderr)

    # -------------------------------------------------------------------------
    # 3. Setup Apt Lock Timeout Behavioural Test
    # -------------------------------------------------------------------------

    def test_setup_wait_for_apt_locks_times_out_and_returns_error(self):
        """Production wait_for_apt_locks with exceeded timeout outputs error and terminates loop with return 1."""
        test_script = f"""
source "{self.project_dir.as_posix()}/scripts/setup.sh" >/dev/null 2>&1 || true

check_apt_locked() {{
    return 0
}}

wait_for_apt_locks 1
"""
        proc = subprocess.run(["bash", "-c", test_script], capture_output=True, text=True, check=False)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("Не удалось дождаться освобождения apt/dpkg lock", proc.stdout + proc.stderr)

    # -------------------------------------------------------------------------
    # 4. Restore Confirmation Safety
    # -------------------------------------------------------------------------

    def test_restore_requires_explicit_confirmation_word(self):
        """cmd_restore aborts when confirmation is not 'RESTORE'."""
        proc = self._run_cli_command("restore", "some_backup.tar.gz.age", input_text="no\n")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("Восстановление отменено", proc.stdout + proc.stderr)

    # -------------------------------------------------------------------------
    # 5. Amnezia API Installer / Uninstaller Safety & Multi-Domain Lifecycle
    # -------------------------------------------------------------------------

    def test_setup_amnezia_help_works_without_root(self):
        """setup-amnezia-api.sh --help must succeed without requiring root privileges."""
        amnezia_script = Path(__file__).resolve().parent.parent / "setup-amnezia-api.sh"
        proc = subprocess.run(
            ["bash", str(amnezia_script), "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("Использование: sudo", proc.stdout)

    def test_setup_amnezia_uninstall_exits_without_running_setup(self):
        """setup-amnezia-api.sh --uninstall terminates immediately and never continues into setup."""
        amnezia_script = Path(__file__).resolve().parent.parent / "setup-amnezia-api.sh"
        test_script = f"""
source "{amnezia_script.as_posix()}"
check_root() {{
    return 0
}}
init_logging() {{
    return 0
}}
main --uninstall
"""
        proc = subprocess.run(
            ["bash", "-c", test_script],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("=== Удаление конфигурации Amnezia API Nginx ===", proc.stdout)
        self.assertIn("Конфигурация не найдена. Нечего удалять.", proc.stdout)
        self.assertNotIn("=== Настройка Amnezia API: Nginx + SSL ===", proc.stdout)

    def test_setup_amnezia_multi_domain_uninstall_lifecycle(self):
        """setup-amnezia-api.sh deletes specified domain and only removes rate-limit zone when last domain is removed."""
        amnezia_script = Path(__file__).resolve().parent.parent / "setup-amnezia-api.sh"
        nginx_dir = self.root / "mock_nginx"
        sites_avail = nginx_dir / "sites-available"
        sites_enable = nginx_dir / "sites-enabled"
        conf_d = nginx_dir / "conf.d"
        sites_avail.mkdir(parents=True)
        sites_enable.mkdir(parents=True)
        conf_d.mkdir(parents=True)

        # Setup Node 1
        node1_avail = sites_avail / "just1kbot-amnezia-node1.example.com"
        node1_avail.write_text("server { server_name node1.example.com; }")
        node1_enable = sites_enable / "just1kbot-amnezia-node1.example.com"
        node1_enable.symlink_to(node1_avail)

        # Setup Node 2
        node2_avail = sites_avail / "just1kbot-amnezia-node2.example.com"
        node2_avail.write_text("server { server_name node2.example.com; }")
        node2_enable = sites_enable / "just1kbot-amnezia-node2.example.com"
        node2_enable.symlink_to(node2_avail)

        # Shared rate limit zone
        rate_limit_conf = conf_d / "just1kbot_amnezia_api_limit.conf"
        rate_limit_conf.write_text("limit_req_zone $binary_remote_addr zone=just1kbot_amnezia_api:10m rate=30r/s;")

        test_script = f"""
source "{amnezia_script.as_posix()}"
check_root() {{
    return 0
}}
init_logging() {{
    return 0
}}
AMNEZIA_NGINX_DIR="{nginx_dir.as_posix()}"
NGINX_DIR="{nginx_dir.as_posix()}"
nginx() {{
    return 0
}}
certbot() {{
    return 0
}}
systemctl() {{
    return 0
}}
main "$@"
"""
        # Step A: Uninstall node1
        proc1 = subprocess.run(
            ["bash", "-c", test_script, "--", "--uninstall", "--domain", "node1.example.com"],
            input="yes\n",
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc1.returncode, 0)
        self.assertFalse(node1_avail.exists())
        self.assertFalse(node1_enable.exists())
        # Node 2 and shared rate limit MUST still exist!
        self.assertTrue(node2_avail.exists())
        self.assertTrue(node2_enable.exists())
        self.assertTrue(rate_limit_conf.exists())

        # Step B: Uninstall node2 (last node)
        proc2 = subprocess.run(
            ["bash", "-c", test_script, "--", "--uninstall", "--domain", "node2.example.com"],
            input="yes\n",
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc2.returncode, 0)
        self.assertFalse(node2_avail.exists())
        self.assertFalse(node2_enable.exists())
        # Now rate limit file MUST be cleaned up!
        self.assertFalse(rate_limit_conf.exists())

    # -------------------------------------------------------------------------
    # 7. Nginx Coexistence and Dual-Mode Reverse Proxy Tests
    # -------------------------------------------------------------------------

    def test_detect_existing_nginx_sites(self):
        """detect_existing_nginx_sites finds user domains, ignores just1k/stock configs."""
        nginx_dir = self.root / "fake_nginx"
        sites_enabled = nginx_dir / "sites-enabled"
        sites_enabled.mkdir(parents=True, exist_ok=True)

        # 1. Default stock placeholder with '_'
        (sites_enabled / "default").write_text("server { listen 80; server_name _; }\n", encoding="utf-8")
        # 2. just1kbot own config (ignored)
        (sites_enabled / "just1kbot.conf").write_text("server { listen 80; server_name bot.example.com; }\n", encoding="utf-8")
        # 3. User custom website
        (sites_enabled / "my-shop.conf").write_text("server { listen 80; server_name myshop.com www.myshop.com; }\n", encoding="utf-8")

        test_script = f"""
source "{CLI_PATH.as_posix()}" >/dev/null 2>&1 || true
detect_existing_nginx_sites "{nginx_dir.as_posix()}"
"""
        proc = subprocess.run(["bash", "-c", test_script], capture_output=True, text=True, check=False)
        self.assertEqual(proc.returncode, 0)
        output = proc.stdout
        self.assertIn("my-shop.conf", output)
        self.assertIn("myshop.com", output)
        self.assertNotIn("just1kbot.conf", output)
        self.assertNotIn("default", output)

    def test_setup_external_nginx_integration_creates_config_and_sets_env(self):
        """setup_external_nginx_integration generates vhost, symlinks to sites-enabled, and updates .env."""
        nginx_dir = self.root / "fake_nginx_setup"
        sites_avail = nginx_dir / "sites-available"
        sites_enb = nginx_dir / "sites-enabled"
        sites_avail.mkdir(parents=True, exist_ok=True)
        sites_enb.mkdir(parents=True, exist_ok=True)

        # Mock nginx executable so nginx -t returns 0
        nginx_bin = self.bin_dir / "nginx"
        nginx_bin.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
        nginx_bin.chmod(0o755)

        env_file = self.project_dir / ".env"
        env_file.write_text("DOMAIN=bot.test.com\nSSL_EMAIL=test@test.com\nBOT_PORT=8080\n", encoding="utf-8")
        env_file.chmod(0o600)

        test_script = f"""
export JUST1KBOT_DIR="{self.project_dir.as_posix()}"
export PROJECT_DIR="{self.project_dir.as_posix()}"
export PATH="{self.bin_dir.as_posix()}:$PATH"
source "{CLI_PATH.as_posix()}" >/dev/null 2>&1 || true
setup_external_nginx_integration "{nginx_dir.as_posix()}"
"""
        proc = subprocess.run(["bash", "-c", test_script], capture_output=True, text=True, check=False)
        self.assertEqual(proc.returncode, 0)
        self.assertTrue((sites_avail / "just1kbot.conf").exists())
        self.assertTrue((sites_enb / "just1kbot.conf").exists())

        vhost_content = (sites_avail / "just1kbot.conf").read_text(encoding="utf-8")
        self.assertIn("bot.test.com", vhost_content)
        self.assertIn("proxy_pass http://127.0.0.1:8080", vhost_content)

        updated_env = env_file.read_text(encoding="utf-8")
        self.assertIn("USE_EXTERNAL_NGINX=true", updated_env)

    def test_setup_external_nginx_integration_rollback_on_nginx_syntax_error(self):
        """setup_external_nginx_integration removes symlink if nginx -t fails."""
        nginx_dir = self.root / "fake_nginx_fail"
        sites_avail = nginx_dir / "sites-available"
        sites_enb = nginx_dir / "sites-enabled"
        sites_avail.mkdir(parents=True, exist_ok=True)
        sites_enb.mkdir(parents=True, exist_ok=True)

        # Mock nginx to simulate syntax error
        nginx_bin = self.bin_dir / "nginx"
        nginx_bin.write_text('#!/bin/bash\necho "nginx syntax error: invalid directive" >&2; exit 1\n', encoding="utf-8")
        nginx_bin.chmod(0o755)

        env_file = self.project_dir / ".env"
        env_file.write_text("DOMAIN=bot.test.com\nSSL_EMAIL=test@test.com\nBOT_PORT=8080\n", encoding="utf-8")
        env_file.chmod(0o600)

        test_script = f"""
export JUST1KBOT_DIR="{self.project_dir.as_posix()}"
export PROJECT_DIR="{self.project_dir.as_posix()}"
export PATH="{self.bin_dir.as_posix()}:$PATH"
source "{CLI_PATH.as_posix()}" >/dev/null 2>&1 || true
setup_external_nginx_integration "{nginx_dir.as_posix()}"
"""
        proc = subprocess.run(["bash", "-c", test_script], capture_output=True, text=True, check=False)
        self.assertEqual(proc.returncode, 1)
        # Symlink in sites-enabled must be removed to avoid breaking existing sites
        self.assertFalse((sites_enb / "just1kbot.conf").exists())
        self.assertIn("JUST1KBOT INFRASTRUCTURE DIAGNOSTIC REPORT", proc.stderr + proc.stdout)

    def test_preflight_external_nginx_mode_skips_caddy_ports(self):
        """When USE_EXTERNAL_NGINX=true, cmd_preflight does not check port 80/443 for Caddy."""
        # Mock nginx executable so nginx -t returns 0
        nginx_bin = self.bin_dir / "nginx"
        nginx_bin.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
        nginx_bin.chmod(0o755)

        # Mock systemctl so nginx is active
        sys_bin = self.bin_dir / "systemctl"
        sys_bin.write_text('#!/bin/bash\nif [[ "$1" == "is-active" && "$3" == "nginx" ]]; then exit 0; fi\nexit 0\n', encoding="utf-8")
        sys_bin.chmod(0o755)

        env_content = (
            "BOT_TOKEN=token123\n"
            "POSTGRES_USER=user\n"
            "POSTGRES_PASSWORD=pass\n"
            "POSTGRES_DB=db\n"
            "DB_ENCRYPTION_KEY=key\n"
            "BACKUP_AGE_RECIPIENT=age1test\n"
            "ADMIN_IDS=[123]\n"
            "DOMAIN=vpn.example.com\n"
            "SSL_EMAIL=admin@example.com\n"
            "SUPPORT_USERNAME=support\n"
            "YOOKASSA_SHOP_ID=123\n"
            "YOOKASSA_SECRET_KEY=sec\n"
            "USE_EXTERNAL_NGINX=true\n"
        )
        (self.project_dir / ".env").write_text(env_content, encoding="utf-8")
        (self.project_dir / ".env").chmod(0o600)

        proc = self._run_cli_command("preflight")
        self.assertEqual(proc.returncode, 0, f"Stdout: {proc.stdout}\nStderr: {proc.stderr}")
        self.assertIn("Режим внешнего Nginx активен (USE_EXTERNAL_NGINX=true)", proc.stdout)

    def test_cmd_backup_creates_restricted_permissions(self):
        """cmd_backup ensures 0700 on backups/ directory and 0600 on created backup files."""
        # Mock docker compose profile tools run --rm backup
        docker_stub = self.bin_dir / "docker"
        docker_stub.write_text(
            '#!/bin/bash\n'
            'if [[ "$1" == "compose" ]] && [[ "$*" =~ "backup" ]]; then\n'
            '    mkdir -p backups\n'
            '    echo "dummy-encrypted-backup-content" > backups/just1kbot_test_backup.sql.gz.age\n'
            '    exit 0\n'
            'fi\n'
            'exit 0\n',
            encoding="utf-8",
        )
        docker_stub.chmod(0o755)

        test_script = f"""
export JUST1KBOT_DIR="{self.project_dir.as_posix()}"
export PROJECT_DIR="{self.project_dir.as_posix()}"
export PATH="{self.bin_dir.as_posix()}:$PATH"
source "{CLI_PATH.as_posix()}" >/dev/null 2>&1 || true
cd "{self.project_dir.as_posix()}"
cmd_backup
"""
        proc = subprocess.run(["bash", "-c", test_script], capture_output=True, text=True, check=False)
        self.assertEqual(proc.returncode, 0, f"cmd_backup failed: {proc.stderr}")
        backups_dir = self.project_dir / "backups"
        self.assertTrue(backups_dir.exists())
        dir_mode = backups_dir.stat().st_mode & 0o777
        self.assertEqual(dir_mode, 0o700, f"Expected 0700 for backups dir, got {oct(dir_mode)}")
        backup_file = backups_dir / "just1kbot_test_backup.sql.gz.age"
        file_mode = backup_file.stat().st_mode & 0o777
        self.assertEqual(file_mode, 0o600, f"Expected 0600 for backup file, got {oct(file_mode)}")

    def test_cmd_update_detached_head_recovers_to_main(self):
        """cmd_update detects detached HEAD and checks out main instead of failing on fetch."""
        work_dir = self._init_git_scenario()
        self._setup_git_work_dir_project(work_dir)

        # Detach HEAD to latest commit
        subprocess.run(["git", "checkout", "--detach", "HEAD"], cwd=work_dir, check=True, capture_output=True)

        test_script = f"""
export PROJECT_DIR="{work_dir.as_posix()}"
export JUST1KBOT_DIR="{work_dir.as_posix()}"
export PATH="{self.bin_dir.as_posix()}:$PATH"
cd "{work_dir.as_posix()}"
source scripts/cli.sh >/dev/null 2>&1 || true

cmd_backup() {{
    LAST_BACKUP_FILE="{work_dir.as_posix()}/dummy.sql.gz.age"
    touch "$LAST_BACKUP_FILE"
    return 0
}}

cmd_update
"""
        proc = subprocess.run(["bash", "-c", test_script], cwd=str(work_dir), capture_output=True, text=True, check=False)
        self.assertEqual(proc.returncode, 0, f"cmd_update failed on detached head: {proc.stdout}\n{proc.stderr}")
        self.assertIn("detached HEAD", proc.stdout)
        self.assertIn("Установлена актуальная версия", proc.stdout)

    def test_cmd_update_non_interactive_dirty_working_tree_fails_closed(self):
        """In non-interactive mode without TTY, dirty working tree outputs AI diagnostic report and exits with code 1."""
        work_dir = self._init_git_scenario()
        self._setup_git_work_dir_project(work_dir)

        # Make local dirty modification
        (work_dir / "uncommitted.txt").write_text("dirty content", encoding="utf-8")
        subprocess.run(["git", "add", "uncommitted.txt"], cwd=work_dir, check=True)

        test_script = f"""
export PROJECT_DIR="{work_dir.as_posix()}"
export JUST1KBOT_DIR="{work_dir.as_posix()}"
export PATH="{self.bin_dir.as_posix()}:$PATH"
cd "{work_dir.as_posix()}"
source scripts/cli.sh >/dev/null 2>&1 || true

cmd_update
"""
        # Execute without stdin to simulate non-interactive cron/headless
        proc = subprocess.run(["bash", "-c", test_script], cwd=str(work_dir), capture_output=True, text=True, check=False)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("JUST1KBOT INFRASTRUCTURE DIAGNOSTIC REPORT", proc.stdout + proc.stderr)
        self.assertIn("Git Working Tree", proc.stdout + proc.stderr)

    def test_cmd_update_non_interactive_local_ahead_fails_closed(self):
        """In non-interactive mode without input, unpushed commits produce AI diagnostic report and exit code 1."""
        work_dir = self._init_git_scenario()
        self._setup_git_work_dir_project(work_dir)
        (work_dir / "local_change.txt").write_text("local only", encoding="utf-8")
        subprocess.run(["git", "add", "local_change.txt"], cwd=work_dir, check=True)
        subprocess.run(["git", "commit", "-m", "Unpushed commit"], cwd=work_dir, check=True)

        test_script = f"""
export PROJECT_DIR="{work_dir.as_posix()}"
export JUST1KBOT_DIR="{work_dir.as_posix()}"
export PATH="{self.bin_dir.as_posix()}:$PATH"
cd "{work_dir.as_posix()}"
source scripts/cli.sh >/dev/null 2>&1 || true

cmd_backup() {{
    LAST_BACKUP_FILE="{work_dir.as_posix()}/dummy.sql.gz.age"
    touch "$LAST_BACKUP_FILE"
    return 0
}}

cmd_update
"""
        proc = subprocess.run(["bash", "-c", test_script], cwd=str(work_dir), capture_output=True, text=True, check=False)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("JUST1KBOT INFRASTRUCTURE DIAGNOSTIC REPORT", proc.stdout + proc.stderr)
        self.assertIn("Git Synchronization", proc.stdout + proc.stderr)
        self.assertIn("опережает", proc.stdout + proc.stderr)

    def test_cmd_update_non_interactive_diverged_fails_closed(self):
        """In non-interactive mode without input, diverged branch produces AI diagnostic report and exit code 1."""
        work_dir = self._init_git_scenario()
        self._setup_git_work_dir_project(work_dir)

        other_dir = self.root / "other2"
        upstream_dir = self.root / "upstream.git"
        subprocess.run(["git", "clone", "-b", "main", str(upstream_dir), str(other_dir)], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "audit3@test.local"], cwd=other_dir, check=True)
        subprocess.run(["git", "config", "user.name", "Audit Runner 3"], cwd=other_dir, check=True)

        (other_dir / "remote_change.txt").write_text("remote change", encoding="utf-8")
        subprocess.run(["git", "add", "remote_change.txt"], cwd=other_dir, check=True)
        subprocess.run(["git", "commit", "-m", "Remote change"], cwd=other_dir, check=True)
        subprocess.run(["git", "push", "origin", "main"], cwd=other_dir, check=True)

        (work_dir / "diverged_local.txt").write_text("diverged local", encoding="utf-8")
        subprocess.run(["git", "add", "diverged_local.txt"], cwd=work_dir, check=True)
        subprocess.run(["git", "commit", "-m", "Local diverged commit"], cwd=work_dir, check=True)

        test_script = f"""
export PROJECT_DIR="{work_dir.as_posix()}"
export JUST1KBOT_DIR="{work_dir.as_posix()}"
export PATH="{self.bin_dir.as_posix()}:$PATH"
cd "{work_dir.as_posix()}"
source scripts/cli.sh >/dev/null 2>&1 || true

cmd_backup() {{
    LAST_BACKUP_FILE="{work_dir.as_posix()}/dummy.sql.gz.age"
    touch "$LAST_BACKUP_FILE"
    return 0
}}

cmd_update
"""
        proc = subprocess.run(["bash", "-c", test_script], cwd=str(work_dir), capture_output=True, text=True, check=False)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("JUST1KBOT INFRASTRUCTURE DIAGNOSTIC REPORT", proc.stdout + proc.stderr)
        self.assertIn("Diverged", proc.stdout + proc.stderr)



class SetupScriptErrorSemanticsTests(unittest.TestCase):
    """Regression guard: `error()` in scripts/setup.sh must abort the whole
    installer (exit 1). This is what makes the sysctl-overcommit failure path
    fail-closed instead of a false "настроено" success."""

    def test_error_function_exits_installer(self):
        repo_script = Path(__file__).resolve().parent.parent / "scripts" / "setup.sh"
        with tempfile.TemporaryDirectory() as tmp:
            shutil.copy(repo_script, Path(tmp) / "setup.sh")
            proc = subprocess.run(
                [
                    "bash",
                    "-c",
                    ". './setup.sh'; error 'boom-marker'; echo NOT_REACHED",
                ],
                capture_output=True,
                text=True,
                cwd=tmp,
                check=False,
            )
        self.assertEqual(proc.returncode, 1)
        self.assertNotIn("NOT_REACHED", proc.stdout)
        self.assertIn("boom-marker", proc.stderr)


class SetupOvercommitPersistenceTests(unittest.TestCase):
    """`configure_overcommit_memory` must pin the persistence VALUE 1
    UNCONDITIONALLY — including the scenario `runtime=1 + persistent=0`
    (e.g. the operator ran `sysctl -w` manually before the installer),
    which used to revert on reboot. Runtime stubbing keeps these tests
    rootless: `sysctl -w` is only invoked when runtime != 1."""

    def _configure(
        self,
        runtime_content: str,
        initial_conf: str,
        *,
        fake_sysctl_exit: int | None = None,
    ) -> tuple[int, str, str, bool]:
        """Run configure_overcommit_memory against stubs.

        Returns (exit_code, sysctl.conf content, sysctl.d/99 content,
        sysctl_was_invoked). When `fake_sysctl_exit` is set, a recording
        `sysctl` shim is placed on PATH so the test can observe whether the
        runtime apply was attempted and control its exit status - all
        rootless.
        """
        repo_script = Path(__file__).resolve().parent.parent / "scripts" / "setup.sh"
        workdir = tempfile.mkdtemp(prefix="oc_overcommit_")
        self.addCleanup(shutil.rmtree, workdir, ignore_errors=True)
        shutil.copy(repo_script, Path(workdir) / "setup.sh")
        proc_stub = Path(workdir) / "proc_overcommit"
        proc_stub.write_text(runtime_content, encoding="utf-8")
        conf = Path(workdir) / "sysctl.conf"
        conf.write_text(initial_conf, encoding="utf-8")

        path_prefix = ""
        if fake_sysctl_exit is not None:
            bin_dir = Path(workdir) / "bin"
            bin_dir.mkdir()
            shim = bin_dir / "sysctl"
            # Relative paths only: the subprocess cwd maps into the WSL/interop
            # filesystem, absolute Windows paths would be invisible there.
            shim.write_text(
                "#!/bin/bash\necho called >> ./sysctl_calls\n"
                f"exit {fake_sysctl_exit}\n",
                encoding="utf-8",
            )
            shim.chmod(0o755)
            path_prefix = 'export PATH="$PWD/bin:$PATH"; '

        proc = subprocess.run(
            [
                "bash",
                "-c",
                f"{path_prefix}"
                ". './setup.sh'; "
                f"JUST1KBOT_PROC_OVERCOMMIT='./proc_overcommit' "
                f"JUST1KBOT_SYSCTL_D_CONF='./sysctl.d/99-just1kbot.conf' "
                f"JUST1KBOT_SYSCTL_CONF='./sysctl.conf' "
                "configure_overcommit_memory",
            ],
            capture_output=True,
            text=True,
            cwd=workdir,
            check=False,
        )
        sysctl_invoked = (Path(workdir) / "sysctl_calls").exists()
        d_conf = Path(workdir) / "sysctl.d" / "99-just1kbot.conf"
        d_content = d_conf.read_text(encoding="utf-8") if d_conf.exists() else ""
        return proc.returncode, conf.read_text(encoding="utf-8"), d_content, sysctl_invoked

    def test_runtime_one_with_persistent_zero_is_repaired(self):
        """The review-requested regression: runtime=1 + persistent=0 must be
        repaired by the installer (reboot would otherwise revert it)."""
        code, content, d_content, sysctl_invoked = self._configure(
            "1", "vm.overcommit_memory = 0\n"
        )
        self.assertEqual(code, 0)
        self.assertIn("vm.overcommit_memory = 1", content)
        self.assertNotIn("= 0", content.replace("vm.overcommit_memory = 1", ""))
        # systemd boot source: /etc/sysctl.d/99-just1kbot.conf pinned to 1.
        self.assertIn("vm.overcommit_memory = 1", d_content)
        # runtime already 1 → no provider-side sysctl apply attempted.
        self.assertFalse(sysctl_invoked)

    def test_runtime_one_with_missing_entry_is_appended(self):
        code, content, d_content, _ = self._configure("1", "# some other setting = 5\n")
        self.assertEqual(code, 0)
        self.assertIn("vm.overcommit_memory = 1", content)
        self.assertIn("vm.overcommit_memory = 1", d_content)

    def test_runtime_one_with_correct_persistence_is_preserved(self):
        code, content, d_content, _ = self._configure("1", "vm.overcommit_memory = 1\n")
        self.assertEqual(code, 0)
        self.assertEqual(content.count("vm.overcommit_memory"), 1)
        self.assertEqual(d_content.count("vm.overcommit_memory"), 1)

    def test_mixed_duplicate_entries_are_normalized_to_single_one(self):
        """A `= 1` line followed by a later `= 0` line would win when sysctl
        applies the file sequentially. The normalizer must collapse ALL
        entries into a single authoritative `= 1`."""
        code, content, d_content, _ = self._configure(
            "1", "vm.overcommit_memory = 1\nother = 7\nvm.overcommit_memory = 0\n"
        )
        self.assertEqual(code, 0)
        matches = [
            line
            for line in content.splitlines()
            if line.strip().startswith("vm.overcommit_memory")
        ]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].strip(), "vm.overcommit_memory = 1")
        self.assertEqual(d_content.count("vm.overcommit_memory"), 1)

    def test_runtime_zero_sysctl_failure_is_fail_closed(self):
        """runtime=0 + failing `sysctl -w` must abort the installer (error →
        exit 1) without touching persistence - no false success."""
        code, content, d_content, sysctl_invoked = self._configure(
            "0",
            "vm.overcommit_memory = 0\n",
            fake_sysctl_exit=1,
        )
        self.assertEqual(code, 1)
        self.assertTrue(sysctl_invoked)
        # Persistence must NOT be silently "configured" after a failed apply.
        self.assertIn("vm.overcommit_memory = 0", content)
        self.assertEqual(d_content, "")


if __name__ == "__main__":
    unittest.main()


