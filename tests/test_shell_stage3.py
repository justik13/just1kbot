import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
CORE = ROOT / "scripts" / "lib" / "deploy_core.inc"
PLATFORM = ROOT / "scripts" / "lib" / "install_safe_platform.sh"
RUNTIME = ROOT / "scripts" / "lib" / "install_safe_runtime.sh"


class ShellStage3Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.deploy = "\n".join(
            path.read_text(encoding="utf-8") for path in (CORE, PLATFORM, RUNTIME)
        )
        cls.workflow = (ROOT / ".github" / "workflows" / "tests.yml").read_text(
            encoding="utf-8"
        )

    def test_live_release_is_root_owned_and_read_only_for_service(self):
        for marker in (
            'chown -R root:"$BOT_USER" "$PROJECT_DIR"',
            "Symlink вне virtualenv запрещён",
            "ReadOnlyPaths=$PROJECT_DIR",
            "ReadWritePaths=$RUNTIME_DIR /var/log/just1kbot",
            "PYTHONDONTWRITEBYTECODE=1",
            'old="${VENV_DIR}.old.$$"',
            'python3 -m venv "$VENV_DIR"',
        ):
            self.assertIn(marker, self.deploy)
        self.assertNotIn("ReadWritePaths=$PROJECT_DIR", self.deploy)
        self.assertIn("предыдущий восстановлен", self.deploy)

    def test_heartbeat_uses_systemd_runtime_directory(self):
        for marker in (
            "RUNTIME_DIR=/run/just1kbot",
            'HEARTBEAT_FILE="$RUNTIME_DIR/heartbeat"',
            "RuntimeDirectory=just1kbot",
            "RuntimeDirectoryMode=0750",
            "JUST1KBOT_HEARTBEAT_FILE=$HEARTBEAT_FILE",
        ):
            self.assertIn(marker, self.deploy)

    def test_healthcheck_is_bounded_and_lock_contention_fails(self):
        for marker in (
            "flock -s -w 5 8",
            "[[ -e /proc/self/fd/200 ]]",
            "timeout --signal=TERM --kill-after=5s 25s",
            "TimeoutStartSec=35s",
            'connect_args={"timeout": 5, "command_timeout": 5}',
            "socket_connect_timeout=5",
            "socket_timeout=5",
        ):
            self.assertIn(marker, self.deploy)
        self.assertNotIn("flock -n 9 || exit 0", self.deploy)
        self.assertNotIn("rollback_heartbeat=obsolete", self.deploy)
        self.assertNotIn('HEARTBEAT_FILE="$PROJECT_DIR/.heartbeat"', self.deploy)

    def test_ci_runs_shellcheck_for_repository_scripts(self):
        self.assertIn("shellcheck", self.workflow)
        self.assertIn("find . -type f -name '*.sh' -print0", self.workflow)
        self.assertIn("shellcheck --severity=error", self.workflow)


if __name__ == "__main__":
    unittest.main()
