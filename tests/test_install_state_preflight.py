import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
CONTROL = (ROOT / "scripts" / "lib" / "control_plane.sh").read_text(
    encoding="utf-8"
)
PREFLIGHT = (ROOT / "scripts" / "preflight_install_state.sh").read_text(
    encoding="utf-8"
)
UNINSTALL_ENTRYPOINT = (ROOT / "scripts" / "uninstall_entrypoint.sh").read_text(
    encoding="utf-8"
)
RUNTIME = (ROOT / "scripts" / "lib" / "install_safe_runtime.sh").read_text(
    encoding="utf-8"
)
SETTINGS = (ROOT / "config" / "settings.py").read_text(encoding="utf-8")


class InstallStatePreflightTests(unittest.TestCase):
    def test_official_update_and_deploy_run_read_only_preflight_first(self):
        for command, next_command in (("update", "deploy"), ("deploy", "install-recover")):
            case = CONTROL[
                CONTROL.index(f"        {command})") : CONTROL.index(
                    f"        {next_command})"
                )
            ]
            self.assertLess(case.index("preflight"), case.index("call_script"))

        preflight_function = CONTROL[
            CONTROL.index("preflight()") : CONTROL.index("smoke()")
        ]
        self.assertLess(
            preflight_function.index("state --operation deploy --require-safe"),
            preflight_function.index("preflight_install_state.sh"),
        )

    def test_preflight_is_strictly_read_only(self):
        mutating_prefixes = (
            "rm ",
            "mv ",
            "chown ",
            "chmod ",
            "install ",
            "useradd ",
            "usermod ",
            "systemctl start ",
            "systemctl enable ",
            "systemctl restart ",
            "apt-get ",
        )
        executable_lines = [
            line.lstrip()
            for line in PREFLIGHT.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        for prefix in mutating_prefixes:
            with self.subTest(prefix=prefix):
                self.assertFalse(
                    any(line.startswith(prefix) for line in executable_lines),
                    prefix,
                )
        self.assertIn("read-only checks пройдены; сервер не изменён", PREFLIGHT)

    def test_preflight_checks_platform_manifest_journal_postgres_and_redis(self):
        for marker in (
            "foundation_assert_ubuntu_2404",
            "foundation_preflight_static_resources",
            "foundation_manifest_validate",
            "foundation_journal_validate",
            "install-recover",
            "install-rollback",
            "pg_select_cluster",
            "pg_assert_existing_database",
            "redis-cli",
            "Redis PING failed",
        ):
            self.assertIn(marker, PREFLIGHT)

    def test_runtime_has_protected_home_and_hard_redis_dependency(self):
        for marker in (
            "ProtectHome=true",
            "Environment=HOME=$RUNTIME_DIR",
            "Requires=$PG_UNIT $REDIS_SERVICE",
            "Requires=just1kbot-redis.service",
            "systemctl is-active --quiet just1kbot-redis.service",
        ):
            self.assertIn(marker, RUNTIME)
        self.assertIn('_SERVICE_RUNTIME_HOME = "/run/just1kbot"', SETTINGS)
        self.assertIn('os.environ["HOME"] = _SERVICE_RUNTIME_HOME', SETTINGS)

    def test_official_uninstall_is_manifest_driven(self):
        self.assertIn("uninstall_foundation.sh", UNINSTALL_ENTRYPOINT)
        self.assertIn('exec /bin/bash "$TARGET" "$@"', UNINSTALL_ENTRYPOINT)
        self.assertNotIn("rm -rf", UNINSTALL_ENTRYPOINT)


if __name__ == "__main__":
    unittest.main()
