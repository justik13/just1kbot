import os
import pwd
import shlex
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts/ops/deploy_application.sh"


class DeployRollbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.active = self.tmp / "active"
        self.source = self.tmp / "source"
        self.snapshots = self.tmp / "snapshots"
        self.unit = self.tmp / "unit.service"
        self.state = self.tmp / "adapter-state"
        self.active.mkdir()
        self.source.mkdir()
        (self.active / "app.txt").write_text("previous\n")
        (self.active / ".env").write_text("TOKEN=CANARY_SECRET\n")
        os.chmod(self.active / ".env", 0o600)
        (self.active / "venv").mkdir()
        (self.active / "venv/runtime").write_text("previous-runtime\n")
        (self.source / "app.txt").write_text("new\n")
        self.unit.write_text("old unit\n")
        self.backup = self.tmp / "encrypted-backups"
        self.backup.mkdir()
        (self.backup / "keep.age").write_text("backup")
        self.adapter = self.tmp / "adapter.sh"
        self.adapter.write_text(
            textwrap.dedent("""\
            #!/bin/bash
            set -u
            state_dir="$ADAPTER_STATE"; mkdir -p "$state_dir"
            gen=$(cat "$state_dir/gen" 2>/dev/null || echo 0)
            calls=$(cat "$state_dir/calls" 2>/dev/null || echo 0)
            case "$1" in
              start)
                echo start >> "$EVENTS"
                # Match systemd: start is a no-op while already active.
                if [[ ! -f "$state_dir/stopped" && "$gen" -gt 0 ]]; then exit 0; fi
                gen=$((gen+1)); echo "$gen" > "$state_dir/gen"; echo 0 > "$state_dir/calls"; rm -f "$state_dir/stopped" ;;
              stop)
                echo stop >> "$EVENTS"
                [[ "${ADAPTER_MODE:-}" == stop_fail ]] || echo inactive > "$state_dir/stopped" ;;
              state)
                if [[ -f "$state_dir/stopped" || "${ADAPTER_MODE:-}" == initial && "$gen" -eq 0 ]]; then echo inactive; exit; fi
                calls=$((calls+1)); echo "$calls" > "$state_dir/calls"
                mode="${ADAPTER_MODE:-success}"
                [[ "$mode" == initial ]] && mode=success
                [[ "$gen" -gt 1 ]] && mode="${ROLLBACK_MODE:-success}"
                if [[ "$mode" == crash && "$calls" -gt 1 ]]; then echo failed
                else
                  if [[ "$mode" == success ]]; then touch -d "@$(( $(date +%s) + calls ))" "$HEARTBEAT_FILE"; fi
                  echo active
                fi ;;
              nrestarts)
                if [[ "${ADAPTER_MODE:-}" == restarts && "$gen" -eq 1 && "$calls" -gt 0 ]]; then echo 1; else echo 0; fi ;;
              mainpid) if [[ -f "$state_dir/stopped" || "${ADAPTER_MODE:-}" == initial && "$gen" -eq 0 ]]; then echo 0; else echo $((1000+gen)); fi ;;
              pid-exists) [[ "${ADAPTER_MODE:-}" == stop_fail && "$2" -eq 1000 ]] ;;
              status) echo 'status TOKEN=CANARY_SECRET postgresql://user:pass@host/db' ;;
              journal) echo 'journal BOT_TOKEN=CANARY_SECRET' ;;
              daemon-reload|enable) : ;;
            esac
        """)
        )
        self.adapter.chmod(0o755)
        self.ok = self.tmp / "ok.sh"
        self.ok.write_text("#!/bin/sh\nexit 0\n")
        self.ok.chmod(0o755)
        self.migrate = self.tmp / "migrate.sh"
        self.migrate.write_text(
            '#!/bin/sh\necho migrate >> "$EVENTS"\nexit ${MIGRATE_EXIT:-0}\n'
        )
        self.migrate.chmod(0o755)
        self.activate = self.tmp / "activate.sh"
        self.activate.write_text('#!/bin/sh\necho activate >> "$EVENTS"\nexit 0\n')
        self.activate.chmod(0o755)
        self.events = self.tmp / "events"
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        rsync = self.bin / "rsync"
        rsync.write_text(
            '#!/bin/sh\necho "rsync $*" >> "$EVENTS"\nexec /usr/bin/rsync "$@"\n'
        )
        rsync.chmod(0o755)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_deploy(self, mode="success", rollback="success", migrate=0, extra=None):
        env = os.environ.copy()
        env.update(
            {
                "DEPLOY_TEST_MODE": "1",
                "PROJECT_DIR": str(self.active),
                "SOURCE_DIR": str(self.source),
                "SNAPSHOT_DIR": str(self.snapshots),
                "UNIT_FILE": str(self.unit),
                "SERVICE_NAME": "test",
                "HEARTBEAT_FILE": str(self.active / ".heartbeat"),
                "READINESS_TIMEOUT": "3",
                "READINESS_POLL_INTERVAL": "0.05",
                "HEALTHCHECK_COMMAND": str(self.ok),
                "SERVICE_ADAPTER": str(self.adapter),
                "ADAPTER_STATE": str(self.state),
                "ADAPTER_MODE": mode,
                "PATH": str(self.bin) + ":" + os.environ["PATH"],
                "ROLLBACK_MODE": rollback,
                "TEST_MIGRATION_COMMAND": str(self.migrate),
                "TEST_ACTIVATION_COMMAND": str(self.activate),
                "EVENTS": str(self.events),
                "MIGRATE_EXIT": str(migrate),
            }
        )
        env.update(extra or {})
        return subprocess.run(
            [str(HELPER)],
            env=env,
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
        )

    def test_snapshot_precedes_active_change(self):
        r = self.run_deploy()
        snap = next(self.snapshots.glob("release-*"))
        self.assertEqual((snap / "application/app.txt").read_text(), "previous\n")
        self.assertLess(
            r.stdout.index("stage=snapshot_previous_release"),
            r.stdout.index("stage=prepare_new_release"),
        )

    def test_snapshot_failure_stops_before_delete_sync(self):
        fake = self.tmp / "fail-bin"
        fake.mkdir()
        rs = fake / "rsync"
        rs.write_text('#!/bin/sh\necho "$*" >> "$EVENTS"\nexit 1\n')
        rs.chmod(0o755)
        r = self.run_deploy(extra={"PATH": str(fake) + ":" + os.environ["PATH"]})
        self.assertEqual(r.returncode, 65)
        self.assertEqual(self.active.joinpath("app.txt").read_text(), "previous\n")
        self.assertNotIn("prepare_new_release", r.stdout)

    def test_transient_active_then_crash_loop_fails(self):
        self.assertNotEqual(self.run_deploy("crash").returncode, 0)

    def test_nrestarts_growth_fails(self):
        self.assertNotEqual(self.run_deploy("restarts").returncode, 0)

    def test_old_heartbeat_rejected(self):
        hb = self.active / ".heartbeat"
        hb.touch()
        os.utime(hb, (1, 1))
        self.assertNotEqual(self.run_deploy("old").returncode, 0)

    def test_fresh_advancing_heartbeat_succeeds(self):
        r = self.run_deploy()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("deployment readiness=success", r.stdout)

    def test_failed_new_release_restores_previous(self):
        self.assertIn("result=rolled_back", self.run_deploy("crash").stdout)

    def test_previous_application_content_returns(self):
        self.run_deploy("crash")
        self.assertEqual((self.active / "app.txt").read_text(), "previous\n")

    def test_env_is_unchanged_on_rollback(self):
        before = (self.active / ".env").read_bytes()
        self.run_deploy("crash")
        self.assertEqual((self.active / ".env").read_bytes(), before)

    def test_backup_directory_is_unchanged(self):
        before = (self.backup / "keep.age").read_bytes()
        self.run_deploy("crash")
        self.assertEqual((self.backup / "keep.age").read_bytes(), before)

    def test_healthy_previous_returns_nonzero(self):
        r = self.run_deploy("crash", "success")
        self.assertEqual(r.returncode, 1)
        self.assertIn("previous_release_healthy=true", r.stdout)

    def test_unhealthy_previous_is_critical(self):
        r = self.run_deploy("crash", "crash")
        self.assertEqual(r.returncode, 2)
        self.assertIn("rollback_failed previous_release_healthy=false", r.stdout)

    def test_migration_failure_never_activates_new(self):
        r = self.run_deploy(migrate=7)
        self.assertNotIn(
            "activate", self.events.read_text() if self.events.exists() else ""
        )
        self.assertIn("activation=not_attempted", r.stdout)

    def test_rollback_never_downgrades_database(self):
        r = self.run_deploy("crash")
        self.assertNotIn("downgrade", self.events.read_text())
        self.assertIn("database_downgrade=not_performed", r.stdout)

    def test_success_does_not_report_rollback(self):
        self.assertNotIn("rolled_back", self.run_deploy().stdout)

    def test_failure_does_not_report_success(self):
        self.assertNotIn(
            "deployment readiness=success", self.run_deploy("crash").stdout
        )

    def test_retention_keeps_latest_snapshot(self):
        for i in range(4):
            d = self.snapshots / f"release-old-{i}"
            d.mkdir(parents=True, exist_ok=True)
            os.utime(d, (i + 1, i + 1))
        r = self.run_deploy(extra={"SNAPSHOT_RETENTION": "3"})
        self.assertEqual(r.returncode, 0)
        self.assertTrue(any(self.snapshots.glob("release-*")))
        self.assertEqual((self.active / "app.txt").read_text(), "new\n")

    def test_stop_precedes_first_active_rsync(self):
        r = self.run_deploy()
        events = self.events.read_text()
        self.assertEqual(r.returncode, 0)
        self.assertLess(
            events.index("stop"), events.index("rsync", events.index("stop"))
        )

    def test_start_on_active_service_is_noop(self):
        env = os.environ.copy()
        env.update(
            {
                "ADAPTER_STATE": str(self.state),
                "ADAPTER_MODE": "success",
                "EVENTS": str(self.events),
                "HEARTBEAT_FILE": str(self.active / ".heartbeat"),
            }
        )
        subprocess.run([str(self.adapter), "start"], env=env, check=True)
        first = (self.state / "gen").read_text()
        subprocess.run([str(self.adapter), "start"], env=env, check=True)
        self.assertEqual((self.state / "gen").read_text(), first)

    def test_new_pid_differs_from_old_pid(self):
        r = self.run_deploy()
        self.assertEqual(r.returncode, 0)
        self.assertEqual((self.state / "gen").read_text().strip(), "1")
        self.assertIn("previous_mainpid=1000", r.stdout)

    def test_stop_failure_blocks_all_mutation(self):
        before_app = (self.active / "app.txt").read_bytes()
        before_runtime = (self.active / "venv/runtime").read_bytes()
        r = self.run_deploy("stop_fail")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual((self.active / "app.txt").read_bytes(), before_app)
        self.assertEqual((self.active / "venv/runtime").read_bytes(), before_runtime)
        self.assertNotIn("migrate", self.events.read_text())
        self.assertNotIn("deployment readiness=success", r.stdout)

    def test_rollback_previous_gets_new_pid(self):
        r = self.run_deploy("crash", "success")
        self.assertEqual(r.returncode, 1)
        self.assertEqual((self.state / "gen").read_text().strip(), "2")
        self.assertEqual((self.active / "app.txt").read_text(), "previous\n")

    def test_initial_install_without_active_service(self):
        shutil.rmtree(self.active)
        r = self.run_deploy("initial")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((self.active / "app.txt").read_text(), "new\n")

    def run_deploy_function(self, script, env=None):
        values = os.environ.copy()
        values.update(
            {
                "DEPLOY_FUNCTIONS_ONLY": "1",
                "DEPLOY_TEST_MODE": "1",
                "TEST_REDIS_CONF": str(self.tmp / "redis.conf"),
            }
        )
        values.update(env or {})
        source = shlex.quote(str(ROOT / "deploy.sh"))
        test_log = shlex.quote(str(self.tmp / "deploy.log"))
        command = (
            f'source {source}; LOG_FILE={test_log}; BOT_USER="$(id -un)"; {script}'
        )
        return subprocess.run(
            ["bash", "-c", command],
            env=values,
            text=True,
            capture_output=True,
            check=False,
        )

    def run_unprivileged(self, command, env=None):
        values = os.environ.copy()
        values.update(env or {})
        if os.geteuid() != 0:
            return subprocess.run(
                command, env=values, text=True, capture_output=True, check=False
            )
        if not shutil.which("runuser"):
            self.skipTest("root environment has no runuser")
        try:
            import pwd

            pwd.getpwnam("nobody")
        except (ImportError, KeyError):
            self.skipTest("root environment has no existing unprivileged user")
        return subprocess.run(
            ["runuser", "-u", "nobody", "--", *command],
            env=values,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_nonroot_functions_only_source_loads_definitions(self):
        result = self.run_unprivileged(
            [
                "bash",
                "-c",
                f'source "{ROOT / "deploy.sh"}"; declare -F setup_redis >/dev/null',
            ],
            {"DEPLOY_FUNCTIONS_ONLY": "1", "DEPLOY_TEST_MODE": "1"},
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_nonroot_direct_execution_is_rejected(self):
        result = self.run_unprivileged(
            ["bash", str(ROOT / "deploy.sh"), "--dry-run"],
            {"DEPLOY_FUNCTIONS_ONLY": "0"},
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("root", result.stderr)
        self.assertNotIn("DRY RUN", result.stdout + result.stderr)

    def test_update_preserves_redis_credential(self):
        conf = self.tmp / "redis.conf"
        conf.write_text("bind 127.0.0.1\nrequirepass OLD_CANARY\n")
        ctl = self.bin / "systemctl"
        ctl.write_text("#!/bin/sh\nexit 0\n")
        ctl.chmod(0o755)
        r = self.run_deploy_function(
            "REDIS_PASSWORD=NEW_CANARY; setup_redis false",
            {"PATH": str(self.bin) + ":" + os.environ["PATH"]},
        )
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("requirepass OLD_CANARY", conf.read_text())
        self.assertNotIn("NEW_CANARY", conf.read_text() + r.stdout + r.stderr)

    def test_arbitrary_update_password_cannot_desynchronize_env(self):
        conf = self.tmp / "redis.conf"
        conf.write_text("requirepass EXISTING_VALUE\n")
        ctl = self.bin / "systemctl"
        ctl.write_text("#!/bin/sh\nexit 0\n")
        ctl.chmod(0o755)
        before = (self.active / ".env").read_bytes()
        r = self.run_deploy_function(
            'ENV_FILE="'
            + str(self.active / ".env")
            + '"; REDIS_PASSWORD=UNRELATED_VALUE; determine_install_kind && setup_redis "$INITIAL_INSTALL"',
            {"PATH": str(self.bin) + ":" + os.environ["PATH"]},
        )
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((self.active / ".env").read_bytes(), before)
        self.assertIn("EXISTING_VALUE", conf.read_text())
        self.assertNotIn("UNRELATED_VALUE", conf.read_text() + r.stdout + r.stderr)

    def test_initial_install_env_is_mode_0600(self):
        envfile = self.tmp / "initial.env"
        command = f'ENV_FILE="{envfile}"; BOT_TOKEN=x; DB_PASSWORD=password1; REDIS_PASSWORD=password2; ADMIN_IDS=1; AMNEZIA_API_URL=x; AMNEZIA_API_KEY=; YOOKASSA_SHOP_ID=; YOOKASSA_SECRET_KEY=; DOMAIN=; create_env_if_missing'
        r = self.run_deploy_function(command)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        stat_result = envfile.stat()
        self.assertEqual(stat_result.st_mode & 0o777, 0o600)
        self.assertEqual(
            pwd.getpwuid(stat_result.st_uid).pw_name, pwd.getpwuid(os.geteuid()).pw_name
        )
        self.assertEqual(stat_result.st_mode & 0o077, 0)
        self.assertTrue(envfile.read_text())

    def test_unsafe_existing_env_fails_closed(self):
        os.chmod(self.active / ".env", 0o644)
        r = self.run_deploy_function(
            'ENV_FILE="' + str(self.active / ".env") + '"; determine_install_kind'
        )
        self.assertNotEqual(r.returncode, 0)

    def test_secret_canaries_are_redacted(self):
        r = self.run_deploy("crash")
        self.assertNotIn("CANARY_SECRET", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
