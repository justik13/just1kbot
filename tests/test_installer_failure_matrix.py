import pathlib
import subprocess
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
LIB = ROOT / "scripts" / "lib"
FAILURE = LIB / "install_safe_failure_injection.sh"
ACTIVATION = LIB / "install_safe_activation_policy.sh"


class InstallerFailureMatrixTests(unittest.TestCase):
    def run_activation(self, failpoint: str) -> subprocess.CompletedProcess[str]:
        command = f"""
set -Eeuo pipefail
calls=()
foundation_fail() {{ printf 'FAIL:%s:%s\\n' "$1" "$3"; return 71; }}
foundation_journal_update() {{ calls+=("journal:$1"); }}
foundation_setup_dedicated_redis() {{ calls+=(redis); }}
setup_firewall_initial() {{ calls+=(firewall-noop); }}
install_backup_tooling() {{ calls+=(backup-tooling); }}
install_healthcheck() {{ calls+=(healthcheck); }}
setup_logrotate() {{ calls+=(logrotate); }}
setup_nginx_initial() {{ calls+=(managed-proxy); }}
refresh_existing_nginx() {{ calls+=(refresh-proxy); }}
setup_systemd() {{ calls+=(systemd); }}
foundation_install_cli() {{ calls+=(cli); }}
# install_safe_activation_policy.sh wraps these base lifecycle functions while
# loading. The matrix isolates activate_release_bundle, so provide minimal
# definitions for the unrelated wrappers before sourcing the production module.
automatic_initial_rollback() {{ :; }}
run_deploy() {{ :; }}
recover_install() {{ :; }}
REDIS_PASSWORD=test-password
INITIAL_INSTALL=true
INSTALL_SAFE_FAILURE_INJECTION_SOURCE_ONLY=1
source {str(FAILURE)!r}
INSTALL_SAFE_ACTIVATION_POLICY_SOURCE_ONLY=1
source {str(ACTIVATION)!r}
# The recovery launcher is part of the real activation path. Replace only the
# filesystem-writing helper after sourcing the production module so the matrix
# continues to assert activation ordering/failpoints without mutating files.
install_recovery_cli_launcher() {{ :; }}
JUST1KBOT_FAILPOINT={failpoint!r}
set +e
activate_release_bundle
rc=$?
set -e
printf 'rc=%s\\n' "$rc"
printf 'calls=%s\\n' "$(IFS=,; printf '%s' "${{calls[*]}}")"
"""
        return subprocess.run(
            ["bash", "-c", command],
            text=True,
            capture_output=True,
            check=False,
        )

    def test_each_activation_failpoint_stops_at_exact_boundary(self):
        expected_last_call = {
            "after-dedicated-redis": "firewall-noop",
            "after-operational-tooling": "logrotate",
            "after-proxy-activation": "managed-proxy",
            "after-systemd": "systemd",
            "after-cli": "cli",
        }
        forbidden_after = {
            "after-dedicated-redis": ("backup-tooling", "managed-proxy", "systemd", "cli"),
            "after-operational-tooling": ("managed-proxy", "systemd", "cli"),
            "after-proxy-activation": ("systemd", "cli"),
            "after-systemd": ("cli",),
            "after-cli": (),
        }
        for point, last in expected_last_call.items():
            with self.subTest(point=point):
                result = self.run_activation(point)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("rc=71", result.stdout)
                self.assertIn(f"FAIL:INJECTED_FAILURE:failpoint={point}", result.stdout)
                calls_line = next(
                    line for line in result.stdout.splitlines() if line.startswith("calls=")
                )
                calls = calls_line.removeprefix("calls=").split(",")
                self.assertEqual(calls[-1], last)
                for forbidden in forbidden_after[point]:
                    self.assertNotIn(forbidden, calls)

    def test_activation_succeeds_without_failpoint(self):
        result = self.run_activation("")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("rc=0", result.stdout)
        self.assertIn(
            "calls=journal:dedicated-redis,redis,firewall-noop,backup-tooling,"
            "healthcheck,logrotate,managed-proxy,systemd,cli",
            result.stdout,
        )


if __name__ == "__main__":
    unittest.main()
