import os
import pathlib
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
OPERATIONAL = ROOT / "scripts" / "lib" / "operational_transaction.sh"


class OperationalNginxRollbackTests(unittest.TestCase):
    def _run_restore(self, active_state: str):
        temporary = tempfile.TemporaryDirectory()
        root = pathlib.Path(temporary.name)
        snapshot = root / "snapshot"
        operational = snapshot / "operational"
        operational.mkdir(parents=True)
        calls = root / "systemctl-calls"
        nginx_calls = root / "nginx-calls"
        binary = root / "bin"
        binary.mkdir()

        systemctl = binary / "systemctl"
        systemctl.write_text(
            "#!/bin/bash\n"
            "printf '%s\\n' \"$*\" >> \"$CALLS\"\n"
            "exit 0\n",
            encoding="utf-8",
        )
        systemctl.chmod(0o755)

        nginx = binary / "nginx"
        nginx.write_text(
            "#!/bin/bash\n"
            "printf '%s\\n' \"$*\" >> \"$NGINX_CALLS\"\n"
            "exit 9\n",
            encoding="utf-8",
        )
        nginx.chmod(0o755)

        (operational / "units.tsv").write_text(
            f"enabled\t{active_state}\tnginx.service\n",
            encoding="utf-8",
        )

        command = f"""
set -Eeuo pipefail
deploy_log() {{ :; }}
source {str(OPERATIONAL)!r}
OPERATIONAL_NGINX=true
restore_operational_units {str(snapshot)!r}
"""
        env = os.environ | {
            "PATH": f"{binary}:{os.environ['PATH']}",
            "CALLS": str(calls),
            "NGINX_CALLS": str(nginx_calls),
        }
        result = subprocess.run(
            ["bash", "-c", command],
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        return temporary, result, calls, nginx_calls

    def test_inactive_nginx_is_restored_without_validation_or_start(self):
        temporary, result, calls, nginx_calls = self._run_restore("inactive")
        with temporary:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            lines = calls.read_text(encoding="utf-8").splitlines()
            self.assertNotIn("start nginx.service", lines)
            self.assertFalse(nginx_calls.exists())

    def test_active_nginx_must_validate_before_start(self):
        temporary, result, calls, nginx_calls = self._run_restore("active")
        with temporary:
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(nginx_calls.read_text(encoding="utf-8").strip(), "-t")
            lines = calls.read_text(encoding="utf-8").splitlines()
            self.assertNotIn("start nginx.service", lines)


if __name__ == "__main__":
    unittest.main()
