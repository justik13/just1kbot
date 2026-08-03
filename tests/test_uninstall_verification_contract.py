import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UNINSTALL = ROOT / "scripts" / "uninstall.sh"
ENTRYPOINT = ROOT / "scripts" / "uninstall_entrypoint.sh"
RESOURCE_PREFLIGHT = ROOT / "scripts" / "preflight_uninstall_resources.sh"
VERIFIER = ROOT / "scripts" / "verify_uninstall_state.sh"


class UninstallVerificationContractTests(unittest.TestCase):
    def test_scripts_parse(self):
        for script in (UNINSTALL, ENTRYPOINT, RESOURCE_PREFLIGHT, VERIFIER):
            result = subprocess.run(
                ["bash", "-n", str(script)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_official_entrypoint_checks_ownership_and_verifies_result(self):
        source = ENTRYPOINT.read_text(encoding="utf-8")
        state_check = 'bash "$INSPECT_STATE" --operation uninstall --require-safe'
        self.assertIn("INSPECT_STATE", source)
        self.assertIn(state_check, source)
        self.assertIn("PREFLIGHT_RESOURCES", source)
        self.assertIn('bash "$PREFLIGHT_RESOURCES"', source)
        self.assertIn("VERIFY_UNINSTALL", source)
        self.assertIn('source "$VERIFY_UNINSTALL"', source)
        self.assertIn('verify_uninstall_main "$VERIFY_MODE"', source)
        self.assertLess(
            source.index(state_check),
            source.index('bash "$PREFLIGHT_RESOURCES"'),
        )
        self.assertLess(
            source.index('bash "$PREFLIGHT_RESOURCES"'),
            source.index('bash "$UNINSTALL" "$@"'),
        )
        self.assertLess(
            source.index('source "$VERIFY_UNINSTALL"'),
            source.index('bash "$UNINSTALL" "$@"'),
        )
        self.assertLess(
            source.index('bash "$UNINSTALL" "$@"'),
            source.index('verify_uninstall_main "$VERIFY_MODE"'),
        )

    def test_direct_uninstall_also_runs_resource_preflight_first(self):
        source = UNINSTALL.read_text(encoding="utf-8")
        main = source[source.index("main() {") :]
        self.assertIn("run_resource_preflight", main)
        self.assertLess(
            main.index("run_resource_preflight"),
            main.index("pause_operational_work"),
        )
        self.assertLess(
            main.index("run_resource_preflight"),
            main.index("stop_units"),
        )

    def test_uninstall_does_not_touch_node_or_global_firewall_state(self):
        source = UNINSTALL.read_text(encoding="utf-8")
        for forbidden in (
            "setup-amnezia-api.sh",
            "just1kbot-amnezia",
            "/etc/just1kbot-amnezia.conf",
            "ufw ",
            "certbot delete",
            "docker ",
            " awg ",
            " wg ",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)
        self.assertIn("Удаление не изменяет firewall", source)
        self.assertIn("nginx_site_has_expected_markers", source)
        self.assertIn("site автоматически восстановлен", source)

    def test_resource_preflight_is_read_only(self):
        source = RESOURCE_PREFLIGHT.read_text(encoding="utf-8")
        for forbidden in ("rm", "mv", "chown", "chmod", "userdel", "systemctl stop"):
            with self.subTest(forbidden=forbidden):
                self.assertFalse(
                    any(line.lstrip().startswith(forbidden + " ") for line in source.splitlines())
                )
        self.assertIn("assert_managed_unit", source)
        self.assertIn("assert_root_tool", source)
        self.assertIn("assert_nginx_site", source)

    def test_uninstall_removes_service_account_in_both_modes(self):
        source = UNINSTALL.read_text(encoding="utf-8")
        self.assertIn("remove_service_user", source)
        main = source[source.index("main() {") :]
        self.assertIn("remove_service_user", main)
        self.assertNotIn('[[ "$MODE" == purge ]] && remove_service_user', main)

    def test_verifier_reports_all_leftovers_in_one_run(self):
        source = VERIFIER.read_text(encoding="utf-8")
        self.assertIn("LEFTOVERS=()", source)
        self.assertIn('printf \'  - %s\\n\' "${LEFTOVERS[@]}"', source)
        self.assertIn("Удаление не считается завершённым", source)
        self.assertIn("check_postgresql_purge", source)
        self.assertIn("check_no_running_processes", source)
        self.assertIn("service_user:$BOT_USER", source)
        self.assertIn("VERIFY_UNINSTALL_SOURCE_ONLY", source)
        self.assertIn("verify_uninstall_main", source)

    def test_verifier_is_read_only(self):
        source = VERIFIER.read_text(encoding="utf-8")
        for forbidden in ("rm", "chown", "chmod", "userdel", "dropdb", "redis-cli"):
            with self.subTest(forbidden=forbidden):
                self.assertFalse(
                    any(line.lstrip().startswith(forbidden + " ") for line in source.splitlines())
                )


if __name__ == "__main__":
    unittest.main()
