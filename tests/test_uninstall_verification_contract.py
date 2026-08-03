import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = ROOT / "scripts" / "uninstall_entrypoint.sh"
VERIFIER = ROOT / "scripts" / "verify_uninstall_state.sh"


class UninstallVerificationContractTests(unittest.TestCase):
    def test_scripts_parse(self):
        for script in (ENTRYPOINT, VERIFIER):
            result = subprocess.run(
                ["bash", "-n", str(script)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_official_entrypoint_always_runs_post_uninstall_verifier(self):
        source = ENTRYPOINT.read_text(encoding="utf-8")
        self.assertIn("VERIFY_UNINSTALL", source)
        self.assertIn('bash "$VERIFY_UNINSTALL" "$VERIFY_MODE"', source)
        self.assertLess(
            source.index('bash "$UNINSTALL" "$@"'),
            source.index('bash "$VERIFY_UNINSTALL" "$VERIFY_MODE"'),
        )

    def test_verifier_reports_all_leftovers_in_one_run(self):
        source = VERIFIER.read_text(encoding="utf-8")
        self.assertIn("LEFTOVERS=()", source)
        self.assertIn('printf \'  - %s\\n\' "${LEFTOVERS[@]}"', source)
        self.assertIn("Удаление не считается завершённым", source)
        self.assertIn("check_postgresql_purge", source)
        self.assertIn("check_no_running_processes", source)

    def test_verifier_is_read_only(self):
        source = VERIFIER.read_text(encoding="utf-8")
        for forbidden in ("rm", "chown", "chmod", "userdel", "dropdb", "redis-cli"):
            with self.subTest(forbidden=forbidden):
                self.assertFalse(
                    any(line.lstrip().startswith(forbidden + " ") for line in source.splitlines())
                )


if __name__ == "__main__":
    unittest.main()
