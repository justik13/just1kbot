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
            "#!/bin/bash\n"
            'if [[ "$1" == "info" ]]; then exit 0; fi\n'
            'if [[ "$1" == "compose" && "$2" == "version" ]]; then echo "Docker Compose version v2.27.0"; exit 0; fi\n'
            "exit 1\n",
            encoding="utf-8",
        )
        docker_stub.chmod(0o755)
        os.environ["JUST1KBOT_NO_SUDO"] = "1"

    def tearDown(self):
        os.environ.pop("JUST1KBOT_NO_SUDO", None)
        self.test_dir.cleanup()

    def _run_cli_command(
        self, *args: str, env_vars: dict[str, str] | None = None, input_text: str | None = None
    ) -> subprocess.CompletedProcess:
        """Run cli.sh directly with args inside isolated test project directory."""
        proc_env = os.environ.copy()
        proc_env["PROJECT_DIR"] = self.project_dir.as_posix()
        proc_env["JUST1KBOT_DIR"] = self.project_dir.as_posix()
        proc_env["JUST1KBOT_NO_SUDO"] = "1"
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
        env_content = "BOT_TOKEN=\nPOSTGRES_USER=user\n"
        (self.project_dir / ".env").write_text(env_content, encoding="utf-8")
        (self.project_dir / ".env").chmod(0o600)

        proc = self._run_cli_command("preflight")
        self.assertEqual(proc.returncode, 1)
        self.assertIn(
            "отсутствует или пуста обязательная переменная: BOT_TOKEN", proc.stdout + proc.stderr
        )

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
        subprocess.run(
            ["git", "init", "--bare", str(upstream_dir)], check=True, capture_output=True
        )

        # Clone repo
        work_dir = self.root / "work"
        subprocess.run(
            ["git", "clone", str(upstream_dir), str(work_dir)], check=True, capture_output=True
        )

        subprocess.run(
            ["git", "config", "user.email", "audit@test.local"], cwd=work_dir, check=True
        )
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
        subprocess.run(
            ["git", "add", "scripts", "docker-compose.yml", ".gitignore"], cwd=work_dir, check=True
        )
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
        proc = subprocess.run(
            ["bash", "-c", test_script],
            cwd=str(work_dir),
            input="y\n",
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertIn(
            "Локальные коммиты сохранены в резервной ветке: backup-local-ahead-",
            proc.stdout + proc.stderr,
        )

        # Verify branch exists
        branches = subprocess.run(
            ["git", "branch"], cwd=work_dir, capture_output=True, text=True, check=True
        ).stdout
        self.assertIn("backup-local-ahead-", branches)

    def test_git_sync_diverged_creates_backup_branch(self):
        """When local and upstream have diverged, production cmd_update creates a backup-diverged branch."""
        work_dir = self._init_git_scenario()
        self._setup_git_work_dir_project(work_dir)

        # Clone another working copy to push upstream change
        other_dir = self.root / "other"
        upstream_dir = self.root / "upstream.git"
        subprocess.run(
            ["git", "clone", "-b", "main", str(upstream_dir), str(other_dir)],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "audit2@test.local"], cwd=other_dir, check=True
        )
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
        proc = subprocess.run(
            ["bash", "-c", test_script],
            cwd=str(work_dir),
            input="y\n",
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertIn(
            "Локальная история сохранена в ветке backup-diverged-", proc.stdout + proc.stderr
        )

        # Verify branch exists
        branches = subprocess.run(
            ["git", "branch"], cwd=work_dir, capture_output=True, text=True, check=True
        ).stdout
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
        proc = subprocess.run(
            ["bash", "-c", test_script], capture_output=True, text=True, check=False
        )
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
    # 7. Fail-Closed Port 80/443 Guard and Doctor Checks
    # -------------------------------------------------------------------------

    def test_preflight_fails_closed_when_ports_occupied(self):
        """cmd_preflight fails closed when port 80 or 443 is occupied by a non-docker process."""
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

        # Mock ss to simulate port 80 occupied by apache2
        ss_bin = self.bin_dir / "ss"
        ss_bin.write_text(
            '#!/bin/bash\necho "LISTEN 0 511 0.0.0.0:80 0.0.0.0:* users:((\\"apache2\\",pid=1234,fd=4))"\nexit 0\n',
            encoding="utf-8",
        )
        ss_bin.chmod(0o755)

        proc = self._run_cli_command("preflight")
        self.assertEqual(proc.returncode, 1, f"Stdout: {proc.stdout}\nStderr: {proc.stderr}")
        self.assertIn("JUST1KBOT INFRASTRUCTURE DIAGNOSTIC REPORT", proc.stderr + proc.stdout)
        self.assertIn("Порт для Caddy недоступен", proc.stderr + proc.stdout)

    def test_preflight_fails_closed_when_use_external_nginx_enabled(self):
        """cmd_preflight fails closed with explicit migration error when USE_EXTERNAL_NGINX=true."""
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
        self.assertEqual(proc.returncode, 1)
        self.assertIn("USE_EXTERNAL_NGINX=true", proc.stderr + proc.stdout)
        self.assertIn("Поддержка внешнего Nginx в боте прекращена", proc.stderr + proc.stdout)

    def test_preflight_fails_closed_when_use_external_nginx_quoted(self):
        """cmd_preflight fails closed when USE_EXTERNAL_NGINX is quoted in .env."""
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
            'USE_EXTERNAL_NGINX="true"\n'
        )
        (self.project_dir / ".env").write_text(env_content, encoding="utf-8")
        (self.project_dir / ".env").chmod(0o600)

        proc = self._run_cli_command("preflight")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("Поддержка внешнего Nginx в боте прекращена", proc.stderr + proc.stdout)

    def test_doctor_detects_port_conflict_with_non_docker_process(self):
        """cmd_doctor outputs error when port 80 is occupied by a host non-docker process."""
        ss_bin = self.bin_dir / "ss"
        ss_bin.write_text(
            '#!/bin/bash\necho "LISTEN 0 511 0.0.0.0:80 0.0.0.0:* users:((\\"nginx\\",pid=5678,fd=6))"\nexit 0\n',
            encoding="utf-8",
        )
        ss_bin.chmod(0o755)

        proc = self._run_cli_command("doctor")
        self.assertIn("КОНФЛИКТ ПОРТА", proc.stdout + proc.stderr)
        self.assertIn("занят сторонним процессом хоста", proc.stdout + proc.stderr)

    def test_doctor_reports_caddy_running_when_docker_listens(self):
        """cmd_doctor reports Caddy running when Docker listens on port 80 and container is running."""
        ss_bin = self.bin_dir / "ss"
        ss_bin.write_text(
            '#!/bin/bash\necho "LISTEN 0 4096 0.0.0.0:80 0.0.0.0:* users:((\\"docker-proxy\\",pid=222,fd=4))"\nexit 0\n',
            encoding="utf-8",
        )
        ss_bin.chmod(0o755)

        docker_bin = self.bin_dir / "docker"
        docker_bin.write_text(
            '#!/bin/bash\nif [[ "$1" == "inspect" ]] && [[ "$*" =~ "just1kbot_caddy" ]]; then echo "running"; exit 0; fi\nexit 0\n',
            encoding="utf-8",
        )
        docker_bin.chmod(0o755)

        proc = self._run_cli_command("doctor")
        self.assertIn("Порт 80 слушается веб-сервером Caddy (just1kbot_caddy: running)", proc.stdout)

    def test_doctor_reports_gdrive_status_and_permissions(self):
        """cmd_doctor reports gdrive status and warns on missing config or wrong permissions."""
        env_file = self.project_dir / ".env"
        env_file.write_text("GDRIVE_BACKUP_ENABLED=true\n", encoding="utf-8")

        # Case 1: Missing rclone.conf
        proc = self._run_cli_command("doctor")
        self.assertIn("файл backups/rclone.conf не найден", proc.stdout + proc.stderr)

        # Case 2: Config present and valid
        backups_dir = self.project_dir / "backups"
        backups_dir.mkdir(parents=True, exist_ok=True)
        rclone_conf = backups_dir / "rclone.conf"
        rclone_conf.write_text(
            "[gdrive]\ntype = drive\nscope = drive\nclient_id = cid\nclient_secret = csec\ntoken = {\"refresh_token\":\"tok\"}\nroot_folder_id = folder123\n",
            encoding="utf-8",
        )
        rclone_conf.chmod(0o600)

        proc = self._run_cli_command("doctor")
        self.assertIn("Google Drive бэкап: настроен (папка ID: folder123", proc.stdout)

    def test_cmd_backup_creates_restricted_permissions(self):
        """cmd_backup ensures 0700 on backups/ directory and 0600 on created backup files."""
        # Mock docker compose profile tools run --rm backup
        docker_stub = self.bin_dir / "docker"
        docker_stub.write_text(
            "#!/bin/bash\n"
            'if [[ "$1" == "compose" ]] && [[ "$*" =~ "backup" ]]; then\n'
            "    mkdir -p backups\n"
            '    echo "dummy-encrypted-backup-content" > backups/just1kbot_test_backup.sql.gz.age\n'
            "    exit 0\n"
            "fi\n"
            "exit 0\n",
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
        proc = subprocess.run(
            ["bash", "-c", test_script], capture_output=True, text=True, check=False
        )
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
        subprocess.run(
            ["git", "checkout", "--detach", "HEAD"], cwd=work_dir, check=True, capture_output=True
        )

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
        proc = subprocess.run(
            ["bash", "-c", test_script],
            cwd=str(work_dir),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            proc.returncode, 0, f"cmd_update failed on detached head: {proc.stdout}\n{proc.stderr}"
        )
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
        proc = subprocess.run(
            ["bash", "-c", test_script],
            cwd=str(work_dir),
            capture_output=True,
            text=True,
            check=False,
        )
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
        proc = subprocess.run(
            ["bash", "-c", test_script],
            cwd=str(work_dir),
            capture_output=True,
            text=True,
            check=False,
        )
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
        subprocess.run(
            ["git", "clone", "-b", "main", str(upstream_dir), str(other_dir)],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "audit3@test.local"], cwd=other_dir, check=True
        )
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
        proc = subprocess.run(
            ["bash", "-c", test_script],
            cwd=str(work_dir),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("JUST1KBOT INFRASTRUCTURE DIAGNOSTIC REPORT", proc.stdout + proc.stderr)
        self.assertIn("Diverged", proc.stdout + proc.stderr)

    def test_cmd_update_cold_deploy_stops_bot_before_migrate_and_audits_invariants(self):
        """cmd_update performs Cold Deploy (stops bot before migrate) and audits invariants post-migration."""
        work_dir = self._init_git_scenario()
        self._setup_git_work_dir_project(work_dir)

        # Clone another copy to push an update upstream
        other_dir = self.root / "other"
        upstream_dir = self.root / "upstream.git"
        subprocess.run(
            ["git", "clone", "-b", "main", str(upstream_dir), str(other_dir)],
            check=True,
            capture_output=True,
        )
        subprocess.run(["git", "config", "user.email", "dev@test.local"], cwd=other_dir, check=True)
        subprocess.run(["git", "config", "user.name", "Developer"], cwd=other_dir, check=True)
        (other_dir / "app_version.txt").write_text("v2.0.0", encoding="utf-8")
        subprocess.run(["git", "add", "app_version.txt"], cwd=other_dir, check=True)
        subprocess.run(["git", "commit", "-m", "Release v2.0.0"], cwd=other_dir, check=True)
        subprocess.run(["git", "push", "origin", "main"], cwd=other_dir, check=True)

        docker_log = self.root / "docker_calls.log"
        docker_stub = self.bin_dir / "docker"
        docker_stub.write_text(
            f"#!/bin/bash\n"
            f'echo "$*" >> "{docker_log.as_posix()}"\n'
            f'if [[ "$1" == "inspect" ]]; then\n'
            f'    if [[ "$*" == *"just1kbot_caddy"* ]]; then echo "running"; elif [[ "$*" == *"just1kbot_migrate"* ]]; then echo "exited/0"; else echo "healthy"; fi\n'
            f'    exit 0\n'
            f'fi\n'
            f"exit 0\n",
            encoding="utf-8",
        )
        docker_stub.chmod(0o755)

        test_script = f"""
export PROJECT_DIR="{work_dir.as_posix()}"
export JUST1KBOT_DIR="{work_dir.as_posix()}"
export JUST1KBOT_NO_SUDO="1"
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
        proc = subprocess.run(
            ["bash", "-c", test_script],
            cwd=str(work_dir),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, f"cmd_update failed: {proc.stdout}\n{proc.stderr}")
        self.assertIn("Остановка сервиса бота перед миграциями (Cold Deploy)", proc.stdout)
        self.assertIn("Применение миграций базы данных", proc.stdout)
        self.assertIn("Проверка инвариантов базы данных", proc.stdout)

        # Check call sequence in docker log
        calls = docker_log.read_text(encoding="utf-8").splitlines()
        stop_idx = next((i for i, line in enumerate(calls) if "compose stop bot" in line), -1)
        migrate_idx = next((i for i, line in enumerate(calls) if "compose run --rm migrate" in line), -1)
        audit_idx = next((i for i, line in enumerate(calls) if "scripts/audit_invariants.py" in line), -1)

        self.assertNotEqual(stop_idx, -1, "docker compose stop bot must be called")
        self.assertNotEqual(migrate_idx, -1, "docker compose run --rm migrate must be called")
        self.assertNotEqual(audit_idx, -1, "scripts/audit_invariants.py must be called")
        self.assertLess(stop_idx, migrate_idx, "Cold Deploy: bot must be stopped before running migrate")
        self.assertLess(migrate_idx, audit_idx, "Invariant check must run after migrate")

    def test_cmd_update_recreates_networks_when_compose_mtu_changes(self):
        """cmd_update runs `compose down` before up when the pulled update changes docker network opts (MTU)."""
        work_dir = self._init_git_scenario()
        self._setup_git_work_dir_project(work_dir)

        # Push upstream an update that only changes docker network MTU
        other_dir = self.root / "other"
        upstream_dir = self.root / "upstream.git"
        subprocess.run(
            ["git", "clone", "-b", "main", str(upstream_dir), str(other_dir)],
            check=True,
            capture_output=True,
        )
        subprocess.run(["git", "config", "user.email", "dev@test.local"], cwd=other_dir, check=True)
        subprocess.run(["git", "config", "user.name", "Developer"], cwd=other_dir, check=True)
        (other_dir / "docker-compose.yml").write_text(
            "services: {}\nnetworks:\n  frontend_net:\n    driver: bridge\n"
            "    driver_opts:\n      com.docker.network.driver.mtu: \"1440\"\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "add", "docker-compose.yml"], cwd=other_dir, check=True)
        subprocess.run(["git", "commit", "-m", "Clamp docker bridge MTU"], cwd=other_dir, check=True)
        subprocess.run(["git", "push", "origin", "main"], cwd=other_dir, check=True)

        docker_log = self.root / "docker_calls.log"
        docker_stub = self.bin_dir / "docker"
        docker_stub.write_text(
            f"#!/bin/bash\n"
            f'echo "$*" >> "{docker_log.as_posix()}"\n'
            f'if [[ "$1" == "inspect" ]]; then\n'
            f'    if [[ "$*" == *"just1kbot_caddy"* ]]; then echo "running"; elif [[ "$*" == *"just1kbot_migrate"* ]]; then echo "exited/0"; else echo "healthy"; fi\n'
            f'    exit 0\n'
            f'fi\n'
            f"exit 0\n",
            encoding="utf-8",
        )
        docker_stub.chmod(0o755)

        test_script = f"""
export PROJECT_DIR="{work_dir.as_posix()}"
export JUST1KBOT_DIR="{work_dir.as_posix()}"
export JUST1KBOT_NO_SUDO="1"
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
        proc = subprocess.run(
            ["bash", "-c", test_script],
            cwd=str(work_dir),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, f"cmd_update failed: {proc.stdout}\n{proc.stderr}")
        self.assertIn("пересоздаю сети", proc.stdout)

        calls = docker_log.read_text(encoding="utf-8").splitlines()
        down_idx = next((i for i, line in enumerate(calls) if "compose down" in line), -1)
        up_idx = next((i for i, line in enumerate(calls) if "up -d" in line), -1)
        self.assertNotEqual(down_idx, -1, "docker compose down must be called on network opts change")
        self.assertNotEqual(up_idx, -1, "docker compose up -d must be called after down")
        self.assertLess(down_idx, up_idx, "Networks must be recreated before starting services")

    def test_cmd_update_keeps_rolling_start_without_compose_network_change(self):
        """cmd_update must NOT run `compose down` when the update does not touch docker network opts."""
        work_dir = self._init_git_scenario()
        self._setup_git_work_dir_project(work_dir)

        other_dir = self.root / "other"
        upstream_dir = self.root / "upstream.git"
        subprocess.run(
            ["git", "clone", "-b", "main", str(upstream_dir), str(other_dir)],
            check=True,
            capture_output=True,
        )
        subprocess.run(["git", "config", "user.email", "dev@test.local"], cwd=other_dir, check=True)
        subprocess.run(["git", "config", "user.name", "Developer"], cwd=other_dir, check=True)
        (other_dir / "app_version.txt").write_text("v2.0.0", encoding="utf-8")
        subprocess.run(["git", "add", "app_version.txt"], cwd=other_dir, check=True)
        subprocess.run(["git", "commit", "-m", "Release v2.0.0"], cwd=other_dir, check=True)
        subprocess.run(["git", "push", "origin", "main"], cwd=other_dir, check=True)

        docker_log = self.root / "docker_calls.log"
        docker_stub = self.bin_dir / "docker"
        docker_stub.write_text(
            f"#!/bin/bash\n"
            f'echo "$*" >> "{docker_log.as_posix()}"\n'
            f'if [[ "$1" == "inspect" ]]; then\n'
            f'    if [[ "$*" == *"just1kbot_caddy"* ]]; then echo "running"; elif [[ "$*" == *"just1kbot_migrate"* ]]; then echo "exited/0"; else echo "healthy"; fi\n'
            f'    exit 0\n'
            f'fi\n'
            f"exit 0\n",
            encoding="utf-8",
        )
        docker_stub.chmod(0o755)

        test_script = f"""
export PROJECT_DIR="{work_dir.as_posix()}"
export JUST1KBOT_DIR="{work_dir.as_posix()}"
export JUST1KBOT_NO_SUDO="1"
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
        proc = subprocess.run(
            ["bash", "-c", test_script],
            cwd=str(work_dir),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, f"cmd_update failed: {proc.stdout}\n{proc.stderr}")

        calls = docker_log.read_text(encoding="utf-8").splitlines()
        down_calls = [line for line in calls if "compose down" in line]
        self.assertEqual(down_calls, [], "rolling update must not tear down containers")

    def test_cmd_update_aborts_and_rolls_back_when_invariant_audit_fails(self):
        """cmd_update aborts rollout and initiates rollback if invariant audit fails."""
        work_dir = self._init_git_scenario()
        self._setup_git_work_dir_project(work_dir)

        # Clone another copy to push an update upstream
        other_dir = self.root / "other"
        upstream_dir = self.root / "upstream.git"
        subprocess.run(
            ["git", "clone", "-b", "main", str(upstream_dir), str(other_dir)],
            check=True,
            capture_output=True,
        )
        subprocess.run(["git", "config", "user.email", "dev@test.local"], cwd=other_dir, check=True)
        subprocess.run(["git", "config", "user.name", "Developer"], cwd=other_dir, check=True)
        (other_dir / "app_version.txt").write_text("v2.0.0", encoding="utf-8")
        subprocess.run(["git", "add", "app_version.txt"], cwd=other_dir, check=True)
        subprocess.run(["git", "commit", "-m", "Release v2.0.0"], cwd=other_dir, check=True)
        subprocess.run(["git", "push", "origin", "main"], cwd=other_dir, check=True)

        docker_stub = self.bin_dir / "docker"
        docker_stub.write_text(
            "#!/bin/bash\n"
            'if [[ "$*" == *"scripts/audit_invariants.py"* ]]; then\n'
            '    echo "Invariant check violation simulated" >&2\n'
            '    exit 1\n'
            'fi\n'
            'if [[ "$1" == "inspect" ]]; then\n'
            '    if [[ "$*" == *"just1kbot_caddy"* ]]; then echo "running"; elif [[ "$*" == *"just1kbot_migrate"* ]]; then echo "exited/0"; else echo "healthy"; fi\n'
            '    exit 0\n'
            'fi\n'
            "exit 0\n",
            encoding="utf-8",
        )
        docker_stub.chmod(0o755)

        test_script = f"""
export PROJECT_DIR="{work_dir.as_posix()}"
export JUST1KBOT_DIR="{work_dir.as_posix()}"
export JUST1KBOT_NO_SUDO="1"
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
        proc = subprocess.run(
            ["bash", "-c", test_script],
            cwd=str(work_dir),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn(
            "Нарушение инвариантов базы данных после применения миграций! Развёртывание прервано.",
            proc.stdout + proc.stderr,
        )
        self.assertIn("Выполняем откат исходного кода к коммиту", proc.stdout + proc.stderr)

    def test_cmd_update_aborts_and_rolls_back_when_stop_bot_fails(self):
        """cmd_update aborts rollout and rolls back if stopping bot fails during Cold Deploy."""
        work_dir = self._init_git_scenario()
        self._setup_git_work_dir_project(work_dir)

        # Clone another copy to push an update upstream
        other_dir = self.root / "other"
        upstream_dir = self.root / "upstream.git"
        subprocess.run(
            ["git", "clone", "-b", "main", str(upstream_dir), str(other_dir)],
            check=True,
            capture_output=True,
        )
        subprocess.run(["git", "config", "user.email", "dev@test.local"], cwd=other_dir, check=True)
        subprocess.run(["git", "config", "user.name", "Developer"], cwd=other_dir, check=True)
        (other_dir / "app_version.txt").write_text("v2.0.0", encoding="utf-8")
        subprocess.run(["git", "add", "app_version.txt"], cwd=other_dir, check=True)
        subprocess.run(["git", "commit", "-m", "Release v2.0.0"], cwd=other_dir, check=True)
        subprocess.run(["git", "push", "origin", "main"], cwd=other_dir, check=True)

        docker_stub = self.bin_dir / "docker"
        docker_stub.write_text(
            "#!/bin/bash\n"
            'if [[ "$*" == *"compose stop bot"* ]]; then\n'
            '    echo "Simulated docker compose stop bot failure" >&2\n'
            '    exit 1\n'
            'fi\n'
            'if [[ "$1" == "inspect" ]]; then\n'
            '    if [[ "$*" == *"just1kbot_caddy"* ]]; then echo "running"; elif [[ "$*" == *"just1kbot_migrate"* ]]; then echo "exited/0"; else echo "healthy"; fi\n'
            '    exit 0\n'
            'fi\n'
            "exit 0\n",
            encoding="utf-8",
        )
        docker_stub.chmod(0o755)

        test_script = f"""
export PROJECT_DIR="{work_dir.as_posix()}"
export JUST1KBOT_DIR="{work_dir.as_posix()}"
export JUST1KBOT_NO_SUDO="1"
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
        proc = subprocess.run(
            ["bash", "-c", test_script],
            cwd=str(work_dir),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn(
            "Ошибка при остановке сервиса бота перед миграциями (Cold Deploy)! Развёртывание прервано.",
            proc.stdout + proc.stderr,
        )
        self.assertIn("Отменяем обновление и возвращаем исходный код к коммиту", proc.stdout + proc.stderr)

    def test_update_force_rebuild_prompt_handling(self):
        """When local_hash == remote_hash, force_rebuild accepts 'y' to proceed or 'n' to exit cleanly."""
        work_dir = self._init_git_scenario()
        self._setup_git_work_dir_project(work_dir)

        # Test answering 'n': should exit 0 with "код уже актуален"
        test_script_n = f"""
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
        proc_n = subprocess.run(
            ["bash", "-c", test_script_n],
            cwd=str(work_dir),
            input="n\n",
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc_n.returncode, 0)
        self.assertIn("Обновление завершено (код уже актуален).", proc_n.stdout + proc_n.stderr)

        # Test answering 'y': should proceed to Step 4/6 (docker build)
        test_script_y = f"""
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
        proc_y = subprocess.run(
            ["bash", "-c", test_script_y],
            cwd=str(work_dir),
            input="y\n",
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertIn(
            "Шаг 4/6. Сборка образов, валидация конфигурации и применение миграций...",
            proc_y.stdout + proc_y.stderr,
        )

    # -------------------------------------------------------------------------
    # 12. Panel version label and entry update check
    # -------------------------------------------------------------------------

    def test_version_command_prints_label(self):
        """`just1kbot version` prints the label and exits 0."""
        proc = self._run_cli_command("version")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("just1kbot v", proc.stdout)

    def test_version_label_uses_pyproject_version(self):
        """bot_version_label reads the version from pyproject.toml (no git in isolation)."""
        (self.project_dir / "pyproject.toml").write_text(
            '[project]\nname = "just1kbot"\nversion = "9.9.9"\n', encoding="utf-8"
        )
        proc = self._run_cli_command("version")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("v9.9.9", proc.stdout)

    def test_update_check_on_entry_is_fail_safe_without_git(self):
        """check_bot_update_on_entry exits 0 silently outside a git repo."""
        script = f"""
export PROJECT_DIR="{self.project_dir.as_posix()}"
export JUST1KBOT_DIR="{self.project_dir.as_posix()}"
export JUST1KBOT_NO_SUDO="1"
source "{self.project_dir.as_posix()}/scripts/cli.sh"
check_bot_update_on_entry
"""
        proc = subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
            cwd=str(self.project_dir),
            env={**os.environ, "JUST1KBOT_NO_SUDO": "1"},
            check=False,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "")

    @unittest.skipUnless(shutil.which("git"), "Git is required for update-check test")
    def test_update_check_on_entry_detects_remote_ahead(self):
        """Notice only when compare API reports remote strictly ahead (mocked, no network)."""
        git_env = self._git_check_env()
        self._init_local_git_project(git_env)
        self._mock_compare_api('{"status":"ahead","ahead_by":2,"behind_by":0}')
        proc = self._run_update_check_snippet()
        self.assertEqual(proc.returncode, 0)
        self.assertIn("Безопасное обновление", proc.stdout)

    @unittest.skipUnless(shutil.which("git"), "Git is required for update-check test")
    def test_update_check_builds_clean_compare_url(self):
        """Origin URL variants (.git suffix, trailing slash, SSH) map to one API path."""
        git_env = self._git_check_env()
        self._init_local_git_project(git_env)
        for origin_url in [
            "https://github.com/justik13/just1kbot.git",
            "https://github.com/justik13/just1kbot",
            "https://github.com/justik13/just1kbot/",
            "git@github.com:justik13/just1kbot.git",
        ]:
            subprocess.run(
                ["git", "remote", "set-url", "origin", origin_url],
                cwd=str(self.project_dir),
                env=git_env,
                check=True,
                capture_output=True,
            )
            self._mock_compare_api('{"status":"ahead","ahead_by":1,"behind_by":0}')
            proc = self._run_update_check_snippet()
            self.assertEqual(proc.returncode, 0, f"failed for origin={origin_url}")
            self.assertIn("Безопасное обновление", proc.stdout, f"no notice for origin={origin_url}")
            argv = self._curl_argv()
            self.assertIn(
                "repos/justik13/just1kbot/compare/", argv, f"bad API URL for origin={origin_url}"
            )
            self.assertNotIn("just1kbot.git/compare", argv, f".git leaked for origin={origin_url}")

    @unittest.skipUnless(shutil.which("git"), "Git is required for update-check test")
    def test_update_check_on_entry_silent_unless_remote_ahead(self):
        """behind/identical/diverged compare statuses stay silent (no false positives)."""
        git_env = self._git_check_env()
        self._init_local_git_project(git_env)
        for status in ["behind", "identical", "diverged"]:
            self._mock_compare_api(f'{{"status":"{status}","ahead_by":0,"behind_by":1}}')
            proc = self._run_update_check_snippet()
            self.assertEqual(proc.returncode, 0, f"failed for status={status}")
            self.assertEqual(proc.stdout.strip(), "", f"notice shown for status={status}")

    @unittest.skipUnless(shutil.which("git"), "Git is required for update-check test")
    def test_update_check_on_entry_silent_for_invalid_api_response(self):
        """Garbage/empty/error API payloads never produce a notice."""
        git_env = self._git_check_env()
        self._init_local_git_project(git_env)
        for payload in ["not json", "", '{"message":"Not Found"}']:
            self._mock_compare_api(payload)
            proc = self._run_update_check_snippet()
            self.assertEqual(proc.returncode, 0, f"failed for payload={payload!r}")
            self.assertEqual(proc.stdout.strip(), "", f"notice shown for payload={payload!r}")

    @unittest.skipUnless(shutil.which("git"), "Git is required for update-check test")
    def test_update_check_makes_no_request_for_non_github_origin(self):
        """Custom (non-github) origins are skipped without any network call."""
        git_env = self._git_check_env()
        self._init_local_git_project(git_env, origin_url="file:///tmp/custom.git")
        marker = self._mock_compare_api('{"status":"ahead"}')
        proc = self._run_update_check_snippet()
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "")
        self.assertFalse(marker.exists(), "curl must not be called for non-github origin")

    def _git_check_env(self):
        """Env with git identity and passthrough `timeout` stub (no real waiting)."""
        timeout_stub = self.bin_dir / "timeout"
        timeout_stub.write_text("#!/bin/bash\nshift\nexec \"$@\"\n", encoding="utf-8")
        timeout_stub.chmod(0o755)
        return {
            **os.environ,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
            "PATH": f"{self.bin_dir.as_posix()}:{os.environ.get('PATH', '')}",
        }

    def _init_local_git_project(self, git_env, origin_url="https://github.com/justik13/just1kbot.git"):
        """Init project_dir on main with one commit; origin URL is never contacted (curl mocked)."""
        subprocess.run(
            ["git", "init", "-b", "main"],
            cwd=str(self.project_dir),
            env=git_env,
            check=True,
            capture_output=True,
        )
        self._git_commit_in(self.project_dir, "1", "base", git_env)
        subprocess.run(
            ["git", "remote", "add", "origin", origin_url],
            cwd=str(self.project_dir),
            env=git_env,
            check=True,
            capture_output=True,
        )

    def _mock_compare_api(self, payload):
        """Stub curl: record the call (marker + argv log), print canned compare-API payload."""
        marker = self.bin_dir / "curl.called"
        argv_log = self.bin_dir / "curl.argv"
        stub = self.bin_dir / "curl"
        stub.write_text(
            f"#!/bin/bash\ntouch \"{marker.as_posix()}\"\n"
            f"printf '%s\\n' \"$@\" > \"{argv_log.as_posix()}\"\n"
            f"printf '%s' '{payload}'\n",
            encoding="utf-8",
        )
        stub.chmod(0o755)
        if marker.exists():
            marker.unlink()
        if argv_log.exists():
            argv_log.unlink()
        return marker

    def _curl_argv(self):
        argv_log = self.bin_dir / "curl.argv"
        return argv_log.read_text(encoding="utf-8") if argv_log.exists() else ""

    def _git_commit_in(self, path, text, message, git_env):
        (path / "probe.txt").write_text(text, encoding="utf-8")
        subprocess.run(
            ["git", "add", "-A"], cwd=str(path), env=git_env, check=True, capture_output=True
        )
        subprocess.run(
            ["git", "commit", "-m", message], cwd=str(path), env=git_env, check=True, capture_output=True
        )

    def _run_update_check_snippet(self):
        script = f"""
export PROJECT_DIR="{self.project_dir.as_posix()}"
export JUST1KBOT_DIR="{self.project_dir.as_posix()}"
export JUST1KBOT_NO_SUDO="1"
export PATH="{self.bin_dir.as_posix()}:$PATH"
source "{self.project_dir.as_posix()}/scripts/cli.sh"
check_bot_update_on_entry
"""
        return subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
            cwd=str(self.project_dir),
            env={
                **os.environ,
                "PATH": f"{self.bin_dir.as_posix()}:{os.environ.get('PATH', '')}",
                "JUST1KBOT_NO_SUDO": "1",
            },
            check=False,
        )

    def _git_state_snapshot(self, git_env):
        refs = subprocess.run(
            ["git", "for-each-ref"],
            cwd=str(self.project_dir),
            env=git_env,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        fetch_head = self.project_dir / ".git" / "FETCH_HEAD"
        head_content = fetch_head.read_text(encoding="utf-8") if fetch_head.exists() else None
        return (refs, head_content)

    @unittest.skipUnless(shutil.which("git"), "Git is required for update-check test")
    def test_update_check_on_entry_silent_when_local_ahead(self):
        """No notice when compare API says remote is behind local (hotfix state)."""
        git_env = self._git_check_env()
        self._init_local_git_project(git_env)
        self._git_commit_in(self.project_dir, "2", "local-hotfix", git_env)
        self._mock_compare_api('{"status":"behind","ahead_by":0,"behind_by":1}')
        proc = self._run_update_check_snippet()
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "")

    @unittest.skipUnless(shutil.which("git"), "Git is required for update-check test")
    def test_update_check_on_entry_silent_when_diverged(self):
        """No notice when histories diverged (updater handles it, not the indicator)."""
        git_env = self._git_check_env()
        self._init_local_git_project(git_env)
        self._git_commit_in(self.project_dir, "2", "local-change", git_env)
        self._mock_compare_api('{"status":"diverged","ahead_by":1,"behind_by":1}')
        proc = self._run_update_check_snippet()
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "")

    @unittest.skipUnless(shutil.which("git"), "Git is required for update-check test")
    def test_update_check_on_entry_detects_behind_from_detached(self):
        """Detached HEAD behind origin/main still reports an available update."""
        git_env = self._git_check_env()
        self._init_local_git_project(git_env)
        self._git_commit_in(self.project_dir, "2", "second", git_env)
        base_sha = subprocess.run(
            ["git", "rev-parse", "HEAD~1"],
            cwd=str(self.project_dir),
            env=git_env,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        subprocess.run(
            ["git", "checkout", base_sha],
            cwd=str(self.project_dir),
            env=git_env,
            check=True,
            capture_output=True,
        )
        self._mock_compare_api('{"status":"ahead","ahead_by":1,"behind_by":0}')
        proc = self._run_update_check_snippet()
        self.assertEqual(proc.returncode, 0)
        self.assertIn("Безопасное обновление", proc.stdout)

    @unittest.skipUnless(shutil.which("git"), "Git is required for update-check test")
    def test_update_check_does_not_modify_git_state(self):
        """Entry check is read-only: refs and FETCH_HEAD unchanged (compare API, no fetch)."""
        git_env = self._git_check_env()
        self._init_local_git_project(git_env)
        self._mock_compare_api('{"status":"ahead","ahead_by":1,"behind_by":0}')
        before = self._git_state_snapshot(git_env)
        proc = self._run_update_check_snippet()
        self.assertEqual(proc.returncode, 0)
        self.assertIn("Безопасное обновление", proc.stdout)
        after = self._git_state_snapshot(git_env)
        self.assertEqual(before, after)


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
        proc_icmp_stub = Path(workdir) / "proc_icmp"
        proc_icmp_stub.write_text("1\n", encoding="utf-8")
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
                f"#!/bin/bash\necho called >> ./sysctl_calls\nexit {fake_sysctl_exit}\n",
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
                f"JUST1KBOT_PROC_ICMP_IGNORE='./proc_icmp' "
                f"JUST1KBOT_SYSCTL_D_CONF='./sysctl.d/99-just1kbot.conf' "
                f"JUST1KBOT_SYSCTL_CONF='./sysctl.conf' "
                "configure_overcommit_memory",
            ],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
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
        self.assertIn("net.ipv4.icmp_echo_ignore_all = 1", d_content)
        # main sysctl.conf must only manage overcommit, not icmp
        self.assertNotIn("net.ipv4.icmp_echo_ignore_all", content)
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
            line for line in content.splitlines() if line.strip().startswith("vm.overcommit_memory")
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


class SetupReadinessAndPortGuardTests(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.test_dir.name)
        self.project_dir = self.root / "just1kbot"
        self.project_dir.mkdir(parents=True, exist_ok=True)
        scripts_dir = self.project_dir / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(CLI_PATH, scripts_dir / "cli.sh")
        shutil.copy(SETUP_PATH, scripts_dir / "setup.sh")
        (scripts_dir / "cli.sh").chmod(0o755)
        (scripts_dir / "setup.sh").chmod(0o755)
        (self.project_dir / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")

        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir(parents=True, exist_ok=True)
        os.environ["JUST1KBOT_NO_SUDO"] = "1"

    def tearDown(self):
        os.environ.pop("JUST1KBOT_NO_SUDO", None)
        self.test_dir.cleanup()

    def test_check_existing_install_loads_domain_cleanly(self):
        """Selecting option 1 in check_existing_install must preserve DOMAIN from .env and set SKIP_WIZARD."""
        env_content = "DOMAIN=bot.test\nBOT_TOKEN=123456:abcdef\n"
        (self.project_dir / ".env").write_text(env_content, encoding="utf-8")
        script = f"""
PROJECT_DIR="{self.project_dir.as_posix()}"
source "{self.project_dir.as_posix()}/scripts/setup.sh"
check_existing_install
echo "LOADED_DOMAIN=$DOMAIN"
echo "SKIP_WIZARD=$SKIP_WIZARD"
"""
        proc = subprocess.run(
            ["bash", "-c", script],
            input="1\n",
            text=True,
            capture_output=True,
            cwd=str(self.project_dir),
            check=False,
        )
        self.assertEqual(proc.returncode, 0, f"check_existing_install failed: {proc.stderr}")
        self.assertIn("LOADED_DOMAIN=bot.test", proc.stdout)
        self.assertIn("SKIP_WIZARD=true", proc.stdout)

    def test_check_existing_install_fails_closed_when_use_external_nginx(self):
        """check_existing_install must fail closed with error when USE_EXTERNAL_NGINX=true is in existing .env."""
        env_content = "DOMAIN=bot.test\nBOT_TOKEN=123456:abcdef\nUSE_EXTERNAL_NGINX=true\n"
        (self.project_dir / ".env").write_text(env_content, encoding="utf-8")
        script = f"""
PROJECT_DIR="{self.project_dir.as_posix()}"
source "{self.project_dir.as_posix()}/scripts/setup.sh"
check_existing_install
"""
        proc = subprocess.run(
            ["bash", "-c", script],
            input="1\n",
            text=True,
            capture_output=True,
            cwd=str(self.project_dir),
            check=False,
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("Поддержка внешнего Nginx в боте прекращена", proc.stderr + proc.stdout)

    def test_setup_port_guard_fails_closed_when_port_busy(self):
        """setup.sh port check must fail closed when port 80/443 is occupied by a non-docker process and cannot be freed."""
        # Mock ss to simulate occupied port 80
        ss_bin = self.bin_dir / "ss"
        ss_bin.write_text(
            '#!/bin/bash\necho "LISTEN 0 511 0.0.0.0:80 0.0.0.0:* users:((\\"custom-daemon\\",pid=999,fd=3))"\nexit 0\n',
            encoding="utf-8",
        )
        script = f"""
export PROJECT_DIR="{self.project_dir.as_posix()}"
export PATH="{self.bin_dir.as_posix()}:$PATH"
source "{self.project_dir.as_posix()}/scripts/setup.sh"
check_ports_available
"""
        proc = subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
            cwd=str(self.project_dir),
            check=False,
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("все еще занят сторонним процессом", proc.stderr + proc.stdout)

    def test_setup_port_guard_accepts_own_caddy_container(self):
        """check_ports_available must succeed without error when ports 80/443 are bound by just1kbot_caddy container."""
        docker_stub = self.bin_dir / "docker"
        docker_stub.write_text(
            '#!/bin/bash\n'
            'if [[ "$1" == "ps" && "$*" =~ "name=^just1kbot_caddy$" ]]; then echo "caddy_cid_123"; exit 0; fi\n'
            'if [[ "$1" == "port" && "$2" == "caddy_cid_123" ]]; then echo "0.0.0.0:$3"; exit 0; fi\n'
            'exit 0\n',
            encoding="utf-8",
        )
        docker_stub.chmod(0o755)

        script = f"""
export PROJECT_DIR="{self.project_dir.as_posix()}"
export PATH="{self.bin_dir.as_posix()}:$PATH"
source "{self.project_dir.as_posix()}/scripts/setup.sh"
check_ports_available
"""
        proc = subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
            cwd=str(self.project_dir),
            check=False,
        )
        self.assertEqual(proc.returncode, 0, f"Failed: {proc.stderr}\n{proc.stdout}")
        self.assertIn("слушается собственным веб-сервером Caddy (just1kbot_caddy: running)", proc.stdout)

    def test_setup_port_guard_does_not_prompt_stopping_unrelated_service(self):
        """check_ports_available must not prompt to stop an unrelated active systemd service when ss shows a different daemon."""
        ss_bin = self.bin_dir / "ss"
        ss_bin.write_text(
            '#!/bin/bash\necho "LISTEN 0 511 0.0.0.0:80 0.0.0.0:* users:((\\"unrelated-app\\",pid=555,fd=3))"\nexit 0\n',
            encoding="utf-8",
        )
        ss_bin.chmod(0o755)

        # Mock systemctl as if nginx is active
        systemctl_bin = self.bin_dir / "systemctl"
        systemctl_bin.write_text(
            '#!/bin/bash\n'
            'if [[ "$1" == "is-active" && "$3" == "nginx" ]]; then exit 0; fi\n'
            'if [[ "$1" == "stop" ]]; then echo "STOP_CALLED" >> /tmp/systemctl_stop.log; exit 0; fi\n'
            'exit 1\n',
            encoding="utf-8",
        )
        systemctl_bin.chmod(0o755)

        script = f"""
export PROJECT_DIR="{self.project_dir.as_posix()}"
export PATH="{self.bin_dir.as_posix()}:$PATH"
source "{self.project_dir.as_posix()}/scripts/setup.sh"
check_ports_available
"""
        proc = subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
            cwd=str(self.project_dir),
            check=False,
        )
        self.assertEqual(proc.returncode, 1)
        self.assertNotIn("Остановить и отключить системную службу 'nginx'", proc.stdout + proc.stderr)
        self.assertIn("все еще занят сторонним процессом", proc.stderr + proc.stdout)

    def test_apply_sysctl_hardening_preserves_custom_settings(self):
        """apply_sysctl_hardening must preserve custom settings in sysctl drop-in
        while pinning vm.overcommit_memory and net.ipv4.icmp_echo_ignore_all to 1."""
        sysctl_dir = self.project_dir / "sysctl.d"
        sysctl_dir.mkdir(parents=True, exist_ok=True)
        conf_file = sysctl_dir / "99-just1kbot.conf"
        conf_file.write_text(
            "# Custom operator configuration\n"
            "custom.security_param = 42\n"
            "vm.overcommit_memory = 0\n",
            encoding="utf-8",
        )

        ufw_dir = self.project_dir / "etc" / "ufw"
        ufw_dir.mkdir(parents=True, exist_ok=True)
        ufw_conf = ufw_dir / "sysctl.conf"
        ufw_conf.write_text(
            "# UFW sysctl configuration\n"
            "# net/ipv4/icmp_echo_ignore_all = 0\n"
            "net/ipv4/icmp_echo_ignore_all = 0\n",
            encoding="utf-8",
        )

        script = """
export PROJECT_DIR="."
export JUST1KBOT_DIR="."
export JUST1KBOT_NO_SUDO="1"
export JUST1KBOT_SYSCTL_D_CONF="./sysctl.d/99-just1kbot.conf"
export JUST1KBOT_UFW_SYSCTL_CONF="./etc/ufw/sysctl.conf"
source "./scripts/cli.sh"

apply_sysctl_hardening
"""
        proc = subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
            cwd=str(self.project_dir),
            env={**os.environ, "JUST1KBOT_NO_SUDO": "1"},
            check=False,
        )
        self.assertEqual(proc.returncode, 0, f"stderr: {proc.stderr}\nstdout: {proc.stdout}")
        content = conf_file.read_text(encoding="utf-8")
        self.assertIn("custom.security_param = 42", content)
        self.assertIn("# Custom operator configuration", content)
        self.assertIn("vm.overcommit_memory = 1", content)
        self.assertIn("net.ipv4.icmp_echo_ignore_all = 1", content)
        self.assertNotIn("vm.overcommit_memory = 0", content)

        ufw_content = ufw_conf.read_text(encoding="utf-8")
        self.assertEqual(ufw_content.count("net/ipv4/icmp_echo_ignore_all=1"), 1)
        self.assertNotIn("net/ipv4/icmp_echo_ignore_all = 0", ufw_content)
        self.assertNotIn("net/ipv4/icmp_echo_ignore_all=0", ufw_content)

    # -------------------------------------------------------------------------
    # 8. Safe Complete Uninstallation Lifecycle Tests
    # -------------------------------------------------------------------------


@unittest.skipUnless(shutil.which("bash"), "Bash is required for shell behavioural tests")
class SafeUninstallationBehaviouralTests(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.test_dir.name)
        self.project_dir = self.root / "just1kbot"
        self.project_dir.mkdir(parents=True, exist_ok=True)

        scripts_dir = self.project_dir / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(CLI_PATH, scripts_dir / "cli.sh")
        shutil.copy(SETUP_PATH, scripts_dir / "setup.sh")
        (scripts_dir / "cli.sh").chmod(0o755)
        (scripts_dir / "setup.sh").chmod(0o755)

        (self.project_dir / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")

        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir(parents=True, exist_ok=True)

        docker_stub = self.bin_dir / "docker"
        docker_stub.write_text(
            "#!/bin/bash\n"
            'if [[ "$1" == "info" ]]; then exit 0; fi\n'
            'if [[ "$1" == "compose" && "$2" == "version" ]]; then echo "Docker Compose version v2.27.0"; exit 0; fi\n'
            "exit 0\n",
            encoding="utf-8",
        )
        docker_stub.chmod(0o755)
        os.environ["JUST1KBOT_NO_SUDO"] = "1"

    def tearDown(self):
        os.environ.pop("JUST1KBOT_NO_SUDO", None)
        self.test_dir.cleanup()

    def _run_cli(self, *args: str, input_text: str | None = None) -> subprocess.CompletedProcess:
        proc_env = os.environ.copy()
        proc_env["PROJECT_DIR"] = self.project_dir.as_posix()
        proc_env["JUST1KBOT_DIR"] = self.project_dir.as_posix()
        proc_env["JUST1KBOT_NO_SUDO"] = "1"
        proc_env["PATH"] = f"{self.bin_dir.as_posix()}:{proc_env.get('PATH', '')}"

        return subprocess.run(
            ["bash", (self.project_dir / "scripts" / "cli.sh").as_posix(), *args],
            cwd=str(self.project_dir),
            input=input_text if input_text is not None else "",
            capture_output=True,
            text=True,
            env=proc_env,
            check=False,
        )

    def test_uninstall_fails_closed_in_non_interactive_mode_without_confirm(self):
        """cmd_uninstall must exit with code 1 when invoked non-interactively without --confirm=DELETE."""
        proc = self._run_cli("uninstall")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("В неинтерактивном режиме для удаления требуется явный флаг", proc.stdout + proc.stderr)
        self.assertTrue(self.project_dir.exists(), "Project directory must not be deleted on fail-closed exit")

    def test_uninstall_fails_when_force_without_confirm_code(self):
        """cmd_uninstall must exit with code 1 when --force is passed without --confirm=DELETE."""
        proc = self._run_cli("uninstall", "--force")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("требуется явное подтверждение: --confirm=DELETE", proc.stdout + proc.stderr)
        self.assertTrue(self.project_dir.exists(), "Project directory must not be deleted on fail-closed exit")

    def test_uninstall_aborts_on_first_confirmation_prompt_refusal(self):
        """cmd_uninstall must abort and exit 0 without removing files when user enters 'n'."""
        proc = self._run_cli("uninstall", input_text="n\n")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("Удаление отменено пользователем", proc.stdout + proc.stderr)
        self.assertTrue(self.project_dir.exists(), "Project directory must not be touched")

    def test_uninstall_aborts_on_second_confirmation_keyword_mismatch(self):
        """cmd_uninstall must abort and exit 0 when the confirmation keyword does not match DELETE or УДАЛИТЬ."""
        proc = self._run_cli("uninstall", input_text="y\nNO\n")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("Подтверждение не совпало", proc.stdout + proc.stderr)
        self.assertTrue(self.project_dir.exists(), "Project directory must not be touched")

    def test_uninstall_full_lifecycle_with_confirm_flag(self):
        """cmd_uninstall with --confirm=DELETE removes all docker resources, crontab, sysctl, wrappers, and project dir."""
        # 1. Prepare fake sysctl file
        sysctl_dir = self.root / "etc" / "sysctl.d"
        sysctl_dir.mkdir(parents=True, exist_ok=True)
        fake_sysctl = sysctl_dir / "99-just1kbot.conf"
        fake_sysctl.write_text("vm.overcommit_memory = 1\n", encoding="utf-8")

        # 1b. Prepare fake ufw sysctl file
        ufw_dir = self.root / "etc" / "ufw"
        ufw_dir.mkdir(parents=True, exist_ok=True)
        fake_ufw = ufw_dir / "sysctl.conf"
        fake_ufw.write_text("net/ipv4/icmp_echo_ignore_all=1\n", encoding="utf-8")

        # 2. Prepare fake wrapper
        fake_bin_dir = self.root / "usr" / "local" / "bin"
        fake_bin_dir.mkdir(parents=True, exist_ok=True)
        fake_wrapper = fake_bin_dir / "just1kbot"
        fake_wrapper.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")

        # 3. Prepare fake crontab
        cron_log = self.root / "crontab.log"
        crontab_mock = self.bin_dir / "crontab"
        crontab_mock.write_text(
            f"""#!/bin/bash
if [[ "$1" == "-l" ]]; then
    if [[ -f "{cron_log.as_posix()}" ]]; then
        cat "{cron_log.as_posix()}"
    else
        echo "0 2 * * * flock -n /tmp/just1kbot-backup.lock sh -c 'cd {self.project_dir.as_posix()}'"
        echo "0 5 * * * other_job"
    fi
    exit 0
fi
if [[ "$1" == "-" ]]; then
    cat > "{cron_log.as_posix()}"
    exit 0
fi
if [[ "$1" == "-r" ]]; then
    rm -f "{cron_log.as_posix()}"
    exit 0
fi
exit 0
""",
            encoding="utf-8",
        )
        crontab_mock.chmod(0o755)

        # 4. Prepare fake docker command that tracks invocation
        docker_log = self.root / "docker_invocations.log"
        (self.bin_dir / "docker").write_text(
            f"""#!/bin/bash
echo "$@" >> "{docker_log.as_posix()}"
if [[ "$1" == "compose" && "$2" == "down" ]]; then exit 0; fi
if [[ "$1" == "ps" ]]; then exit 0; fi
if [[ "$1" == "volume" && "$2" == "ls" ]]; then
    echo "test_project_vol"
    exit 0
fi
if [[ "$1" == "volume" && "$2" == "inspect" ]]; then
    echo "just1kbot"
    exit 0
fi
if [[ "$1" == "volume" ]]; then exit 0; fi
if [[ "$1" == "network" ]]; then exit 0; fi
if [[ "$1" == "images" ]]; then exit 0; fi
if [[ "$1" == "image" ]]; then exit 0; fi
exit 0
""",
            encoding="utf-8",
        )
        (self.bin_dir / "docker").chmod(0o755)

        script = f"""
export PROJECT_DIR="{self.project_dir.as_posix()}"
export JUST1KBOT_DIR="{self.project_dir.as_posix()}"
export JUST1KBOT_NO_SUDO="1"
export JUST1KBOT_SYSCTL_D_CONF="{fake_sysctl.as_posix()}"
export JUST1KBOT_UFW_SYSCTL_CONF="{fake_ufw.as_posix()}"
export JUST1KBOT_GLOBAL_WRAPPER="{fake_wrapper.as_posix()}"
export PATH="{self.bin_dir.as_posix()}:$PATH"
source "{self.project_dir.as_posix()}/scripts/cli.sh"

cmd_uninstall --confirm=DELETE
"""
        proc = subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
            cwd=str(self.project_dir),
            env={**os.environ, "PATH": f"{self.bin_dir.as_posix()}:{os.environ.get('PATH', '')}", "JUST1KBOT_NO_SUDO": "1"},
            check=False,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("Just1kBot успешно и полностью удален с сервера без остатков", proc.stdout)
        self.assertFalse(self.project_dir.exists(), "PROJECT_DIR must be deleted completely")
        self.assertFalse(fake_sysctl.exists(), "sysctl configuration must be deleted")
        self.assertNotIn("net/ipv4/icmp_echo_ignore_all", fake_ufw.read_text(encoding="utf-8"))
        self.assertFalse(fake_wrapper.exists(), "global wrapper must be deleted by cmd_uninstall")
        if cron_log.exists():
            remaining_cron = cron_log.read_text(encoding="utf-8")
            self.assertNotIn("just1kbot-backup.lock", remaining_cron)
            self.assertIn("other_job", remaining_cron)

    def test_uninstall_trailing_confirm_flag_fails_closed_without_parser_crash(self):
        """cmd_uninstall --confirm without value must fail-closed (code 1) and not crash on shift."""
        proc = self._run_cli("uninstall", "--confirm")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("В неинтерактивном режиме для удаления требуется явный флаг", proc.stdout + proc.stderr)
        self.assertNotIn("shift: shift count out of range", proc.stdout + proc.stderr)

    def test_uninstall_backup_copy_failure_aborts_fail_closed(self):
        """When backup copy to safe destination fails, uninstall must abort (code 1) and preserve PROJECT_DIR."""
        backups_dir = self.project_dir / "backups"
        backups_dir.mkdir(parents=True, exist_ok=True)
        (backups_dir / "critical_data.sql.gz.age").write_text("precious_data", encoding="utf-8")

        # Create a file at save path so mkdir -p fails
        conflict_file = self.root / "blocked_dest"
        conflict_file.write_text("blocker", encoding="utf-8")
        invalid_save_dest = conflict_file / "sub_backups"

        script = f"""
export PROJECT_DIR="{self.project_dir.as_posix()}"
export JUST1KBOT_DIR="{self.project_dir.as_posix()}"
export JUST1KBOT_NO_SUDO="1"
export JUST1KBOT_BACKUP_SAVE_DIR="{invalid_save_dest.as_posix()}"
export PATH="{self.bin_dir.as_posix()}:$PATH"
source "{self.project_dir.as_posix()}/scripts/cli.sh"

cmd_uninstall --confirm=DELETE
"""
        proc = subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
            cwd=str(self.project_dir),
            env={**os.environ, "PATH": f"{self.bin_dir.as_posix()}:{os.environ.get('PATH', '')}", "JUST1KBOT_NO_SUDO": "1"},
            check=False,
        )
        self.assertEqual(proc.returncode, 1, "Uninstall must exit 1 when backup preservation fails")
        self.assertIn("Fail-Closed", proc.stdout + proc.stderr)
        self.assertTrue(self.project_dir.exists(), "PROJECT_DIR must NOT be deleted when backup copy fails")
        self.assertTrue((backups_dir / "critical_data.sql.gz.age").exists(), "Original backups must remain intact")

    def test_uninstall_preserves_foreign_docker_volumes(self):
        """cmd_uninstall must only remove volumes belonging to just1kbot compose project."""
        docker_log = self.root / "docker_volumes_tested.log"
        (self.bin_dir / "docker").write_text(
            f"""#!/bin/bash
if [[ "$1" == "compose" ]]; then exit 0; fi
if [[ "$1" == "ps" ]]; then exit 0; fi
if [[ "$1" == "volume" && "$2" == "ls" ]]; then
    echo "just1kbot_postgres_data"
    echo "other_app_postgres_data"
    exit 0
fi
if [[ "$1" == "volume" && "$2" == "inspect" ]]; then
    vol="$5"
    if [[ "$vol" == "just1kbot_postgres_data" ]]; then
        echo "just1kbot"
    else
        echo "unrelated_app"
    fi
    exit 0
fi
if [[ "$1" == "volume" && "$2" == "rm" ]]; then
    echo "RM_VOLUME: $@" >> "{docker_log.as_posix()}"
    exit 0
fi
if [[ "$1" == "network" ]]; then exit 0; fi
if [[ "$1" == "images" ]]; then exit 0; fi
exit 0
""",
            encoding="utf-8",
        )
        (self.bin_dir / "docker").chmod(0o755)

        script = f"""
export PROJECT_DIR="{self.project_dir.as_posix()}"
export JUST1KBOT_DIR="{self.project_dir.as_posix()}"
export JUST1KBOT_NO_SUDO="1"
export PATH="{self.bin_dir.as_posix()}:$PATH"
source "{self.project_dir.as_posix()}/scripts/cli.sh"

cmd_uninstall --confirm=DELETE --purge-backups
"""
        proc = subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
            cwd=str(self.project_dir),
            env={**os.environ, "PATH": f"{self.bin_dir.as_posix()}:{os.environ.get('PATH', '')}", "JUST1KBOT_NO_SUDO": "1"},
            check=False,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertTrue(docker_log.exists())
        log_content = docker_log.read_text(encoding="utf-8")
        self.assertIn("just1kbot_postgres_data", log_content)
        self.assertNotIn("other_app_postgres_data", log_content, "Foreign docker volume must NEVER be removed!")

    def test_uninstall_preserves_backups_when_keep_backups_specified(self):
        """cmd_uninstall preserves backup directory when --keep-backups is given and excludes credentials."""
        backups_dir = self.project_dir / "backups"
        backups_dir.mkdir(parents=True, exist_ok=True)
        (backups_dir / "dump1.sql.gz.age").write_text("encrypted_backup_payload", encoding="utf-8")
        (backups_dir / "rclone.conf").write_text("[gdrive]\ntoken=secret", encoding="utf-8")
        (backups_dir / "service_account.json").write_text('{"private_key":"secret"}', encoding="utf-8")

        saved_dir = self.root / "saved_backups"

        script = f"""
export PROJECT_DIR="{self.project_dir.as_posix()}"
export JUST1KBOT_DIR="{self.project_dir.as_posix()}"
export JUST1KBOT_NO_SUDO="1"
export JUST1KBOT_BACKUP_SAVE_DIR="{saved_dir.as_posix()}"
export PATH="{self.bin_dir.as_posix()}:$PATH"
source "{self.project_dir.as_posix()}/scripts/cli.sh"

cmd_uninstall --confirm=DELETE --keep-backups
"""
        proc = subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
            cwd=str(self.project_dir),
            env={**os.environ, "PATH": f"{self.bin_dir.as_posix()}:{os.environ.get('PATH', '')}", "JUST1KBOT_NO_SUDO": "1"},
            check=False,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertFalse(self.project_dir.exists(), "PROJECT_DIR must be deleted")
        self.assertTrue(saved_dir.exists(), "Saved backups directory must exist")
        self.assertTrue((saved_dir / "dump1.sql.gz.age").exists(), "Backup files must be preserved in save location")
        self.assertFalse((saved_dir / "rclone.conf").exists(), "rclone.conf must NEVER be copied to saved location!")
        self.assertFalse((saved_dir / "service_account.json").exists(), "service_account.json must NEVER be copied to saved location!")

    def test_uninstall_empty_backups_with_keep_backups_succeeds(self):
        """cmd_uninstall --keep-backups must succeed without fail-closed error when backups/ is empty or contains dotfiles."""
        backups_dir = self.project_dir / "backups"
        backups_dir.mkdir(parents=True, exist_ok=True)
        (backups_dir / ".gitkeep").write_text("", encoding="utf-8")

        saved_dir = self.root / "saved_backups_empty"

        script = f"""
export PROJECT_DIR="{self.project_dir.as_posix()}"
export JUST1KBOT_DIR="{self.project_dir.as_posix()}"
export JUST1KBOT_NO_SUDO="1"
export JUST1KBOT_BACKUP_SAVE_DIR="{saved_dir.as_posix()}"
export PATH="{self.bin_dir.as_posix()}:$PATH"
source "{self.project_dir.as_posix()}/scripts/cli.sh"

cmd_uninstall --confirm=DELETE --keep-backups
"""
        proc = subprocess.run(
            ["bash", "-c", script],
            capture_output=True,
            text=True,
            cwd=str(self.project_dir),
            env={**os.environ, "PATH": f"{self.bin_dir.as_posix()}:{os.environ.get('PATH', '')}", "JUST1KBOT_NO_SUDO": "1"},
            check=False,
        )
        self.assertEqual(proc.returncode, 0, f"Uninstall failed: {proc.stderr}\n{proc.stdout}")
        self.assertFalse(self.project_dir.exists(), "PROJECT_DIR must be deleted")
        self.assertTrue(saved_dir.exists(), "Saved backups directory must exist")
        self.assertTrue((saved_dir / ".gitkeep").exists(), ".gitkeep must be copied to save location")

    def test_setup_sh_delegates_to_uninstall(self):
        """scripts/setup.sh --uninstall delegates to cli.sh uninstall."""
        proc = subprocess.run(
            ["bash", (self.project_dir / "scripts" / "setup.sh").as_posix(), "--uninstall", "--force"],
            capture_output=True,
            text=True,
            cwd=str(self.project_dir),
            env={**os.environ, "PROJECT_DIR": self.project_dir.as_posix(), "JUST1KBOT_DIR": self.project_dir.as_posix(), "PATH": f"{self.bin_dir.as_posix()}:{os.environ.get('PATH', '')}", "JUST1KBOT_NO_SUDO": "1"},
            check=False,
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("требуется явное подтверждение: --confirm=DELETE", proc.stdout + proc.stderr)

    def test_read_commands_do_not_redirect_stderr_to_devnull(self):
        """Invariant: No read -p command in any shell script may redirect stderr to /dev/null, which silences prompts."""
        repo_root = CLI_PATH.parent.parent
        target_dirs = [repo_root / "scripts", repo_root / "just1knode"]
        for target_dir in target_dirs:
            for script_path in target_dir.rglob("*.sh"):
                with open(script_path, "r", encoding="utf-8", errors="replace") as f:
                    for idx, line in enumerate(f, 1):
                        stripped = line.strip()
                        if stripped.startswith("#"):
                            continue
                        if "read " in stripped and "2>/dev/null" in stripped:
                            rel_path = script_path.relative_to(repo_root)
                            self.fail(
                                f"Silent prompt anti-pattern found in {rel_path}:{idx}: '{stripped}'. "
                                f"Bash 'read -p' writes prompts to stderr; '2>/dev/null' silences the prompt completely."
                            )

    def test_ufw_delete_redirects_both_stdout_and_stderr(self):
        """Invariant: ufw delete commands must redirect both stdout and stderr (>/dev/null 2>&1), not only 2>/dev/null."""
        repo_root = CLI_PATH.parent.parent
        for target_dir in [repo_root / "scripts", repo_root / "just1knode"]:
            for script_path in target_dir.rglob("*.sh"):
                with open(script_path, "r", encoding="utf-8", errors="replace") as f:
                    for idx, line in enumerate(f, 1):
                        stripped = line.strip()
                        if stripped.startswith("#"):
                            continue
                        if "ufw delete" in stripped and "2>/dev/null" in stripped and ">/dev/null" not in stripped:
                            rel_path = script_path.relative_to(repo_root)
                            self.fail(
                                f"Incomplete ufw delete redirection in {rel_path}:{idx}: '{stripped}'. "
                                f"Must redirect both stdout and stderr (>/dev/null 2>&1) to avoid leaking status text."
                            )

    def test_state_and_watchdog_locks_use_o_nofollow_and_fchmod(self):
        """Invariant: state.sh, traffic_watchdog.sh and traffic_watchdog.py must use O_NOFOLLOW and fd-based fchmod."""
        repo_root = CLI_PATH.parent.parent
        targets = [
            repo_root / "just1knode" / "lib" / "state.sh",
            repo_root / "just1knode" / "lib" / "traffic_watchdog.sh",
            repo_root / "just1knode" / "lib" / "traffic_watchdog.py",
        ]
        for path in targets:
            content = path.read_text(encoding="utf-8")
            self.assertIn("O_NOFOLLOW", content, f"Missing O_NOFOLLOW in {path.name}")
            self.assertIn("fchmod", content, f"Missing fchmod in {path.name}")


if __name__ == "__main__":
    unittest.main()

