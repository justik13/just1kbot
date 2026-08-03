import os
import pathlib
import subprocess
import tempfile
import textwrap
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
POLICY = ROOT / "scripts" / "lib" / "install_safe_package_policy.sh"


class InstallerPackagePolicyRuntimeTests(unittest.TestCase):
    def run_policy(
        self,
        *,
        proxy_mode: str,
        existing_redis: bool = False,
    ) -> tuple[subprocess.CompletedProcess[str], list[str], list[str]]:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            binary = root / "bin"
            state = root / "state"
            binary.mkdir()
            state.mkdir()
            apt_log = root / "apt.log"
            systemctl_log = root / "systemctl.log"

            (binary / "dpkg-query").write_text(
                "#!/bin/bash\n"
                "pkg=${@: -1}\n"
                "if [[ \"${MOCK_EXISTING_REDIS:-0}\" == 1 && \"$pkg\" == redis-server ]]; then\n"
                "  printf 'installed'\n"
                "  exit 0\n"
                "fi\n"
                "exit 1\n",
                encoding="utf-8",
            )
            (binary / "apt-get").write_text(
                "#!/bin/bash\n"
                "printf '%s\\n' \"$*\" >> \"$MOCK_APT_LOG\"\n"
                "[[ \"${1:-}\" != install ]] || touch \"$MOCK_STATE/package-installed\"\n"
                "exit 0\n",
                encoding="utf-8",
            )
            (binary / "systemctl").write_text(
                "#!/bin/bash\n"
                "printf '%s\\n' \"$*\" >> \"$MOCK_SYSTEMCTL_LOG\"\n"
                "present=0\n"
                "[[ \"${MOCK_EXISTING_REDIS:-0}\" == 1 || -f \"$MOCK_STATE/package-installed\" ]] && present=1\n"
                "case \"${1:-}\" in\n"
                "  is-enabled)\n"
                "    if (( present )); then echo enabled; exit 0; fi\n"
                "    echo not-found; exit 4;;\n"
                "  is-active)\n"
                "    if (( present )); then echo active; exit 0; fi\n"
                "    echo inactive; exit 3;;\n"
                "  stop|disable) exit 0;;\n"
                "  *) exit 0;;\n"
                "esac\n",
                encoding="utf-8",
            )
            for path in binary.iterdir():
                path.chmod(0o755)

            command = textwrap.dedent(
                f"""
                set -Eeuo pipefail
                resolve_proxy_mode() {{ :; }}
                installer_set_step() {{ :; }}
                foundation_log() {{ :; }}
                foundation_warn() {{ printf 'WARN:%s\\n' "$*" >&2; }}
                error() {{ printf 'ERROR:%s\\n' "$*" >&2; return 1; }}
                INSTALL_SAFE_PACKAGE_POLICY_SOURCE_ONLY=1
                source {str(POLICY)!r}
                PROXY_MODE={proxy_mode!r}
                install_dependencies
                """
            )
            env = os.environ | {
                "PATH": f"{binary}:{os.environ['PATH']}",
                "MOCK_APT_LOG": str(apt_log),
                "MOCK_SYSTEMCTL_LOG": str(systemctl_log),
                "MOCK_STATE": str(state),
                "MOCK_EXISTING_REDIS": "1" if existing_redis else "0",
            }
            result = subprocess.run(
                ["bash", "-c", command],
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            apt_calls = (
                apt_log.read_text(encoding="utf-8").splitlines()
                if apt_log.exists()
                else []
            )
            systemctl_calls = (
                systemctl_log.read_text(encoding="utf-8").splitlines()
                if systemctl_log.exists()
                else []
            )
        return result, apt_calls, systemctl_calls

    def test_external_proxy_omits_nginx_and_certbot(self):
        result, apt_calls, systemctl_calls = self.run_policy(proxy_mode="external")
        self.assertEqual(result.returncode, 0, result.stderr)
        install = next(call for call in apt_calls if call.startswith("install "))
        self.assertNotIn("nginx", install.split())
        self.assertNotIn("certbot", install.split())
        self.assertIn("redis-server", install.split())
        self.assertIn("stop redis-server.service", systemctl_calls)
        self.assertIn("disable redis-server.service", systemctl_calls)

    def test_managed_proxy_installs_missing_nginx_and_certbot(self):
        result, apt_calls, _ = self.run_policy(proxy_mode="managed")
        self.assertEqual(result.returncode, 0, result.stderr)
        install = next(call for call in apt_calls if call.startswith("install "))
        self.assertIn("nginx", install.split())
        self.assertIn("certbot", install.split())

    def test_existing_global_redis_state_is_not_changed(self):
        result, _, systemctl_calls = self.run_policy(
            proxy_mode="external", existing_redis=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("stop redis-server.service", systemctl_calls)
        self.assertNotIn("disable redis-server.service", systemctl_calls)
        self.assertNotIn("stop redis.service", systemctl_calls)
        self.assertNotIn("disable redis.service", systemctl_calls)


if __name__ == "__main__":
    unittest.main()
