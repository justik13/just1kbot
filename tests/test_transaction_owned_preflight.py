import os
import subprocess
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FOUNDATION = ROOT / "scripts/lib/installer_foundation.sh"
COMPAT = ROOT / "scripts/lib/installer_foundation_compat.sh"


class TransactionOwnedPreflightTests(unittest.TestCase):
    def run_shell(self, script: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["FOUNDATION_PATH"] = str(FOUNDATION)
        env["COMPAT_PATH"] = str(COMPAT)
        return subprocess.run(
            ["bash", "-c", textwrap.dedent(script)],
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )

    def test_current_transaction_owned_path_is_accepted_before_manifest(self) -> None:
        result = self.run_shell(
            r'''
            set -Eeuo pipefail
            export INSTALLER_FOUNDATION_SOURCE_ONLY=1
            source "$FOUNDATION_PATH"
            unset INSTALLER_FOUNDATION_SOURCE_ONLY
            export INSTALLER_FOUNDATION_COMPAT_SOURCE_ONLY=1
            source "$COMPAT_PATH"
            unset INSTALLER_FOUNDATION_COMPAT_SOURCE_ONLY

            foundation_manifest_has_resource() { return 1; }
            foundation_journal_validate() { return 0; }

            tmp=$(mktemp -d)
            trap 'rm -rf "$tmp"' EXIT
            INSTALL_JOURNAL="$tmp/transaction.json"
            resource="$tmp/recovery-bundle"
            mkdir -p "$resource"
            python3 - "$INSTALL_JOURNAL" "path:$resource" <<'PY'
import json
import sys
from pathlib import Path
Path(sys.argv[1]).write_text(json.dumps({"created_resources": [sys.argv[2]]}), encoding="utf-8")
PY

            foundation_preflight_path_absent_or_owned \
                "$resource" "path:$resource" "Recovery bundle"
            ''',
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_foreign_path_still_fails_without_manifest_or_journal_ownership(self) -> None:
        result = self.run_shell(
            r'''
            set -Eeuo pipefail
            export INSTALLER_FOUNDATION_SOURCE_ONLY=1
            source "$FOUNDATION_PATH"
            unset INSTALLER_FOUNDATION_SOURCE_ONLY
            export INSTALLER_FOUNDATION_COMPAT_SOURCE_ONLY=1
            source "$COMPAT_PATH"
            unset INSTALLER_FOUNDATION_COMPAT_SOURCE_ONLY

            foundation_manifest_has_resource() { return 1; }
            foundation_journal_validate() { return 0; }

            tmp=$(mktemp -d)
            trap 'rm -rf "$tmp"' EXIT
            INSTALL_JOURNAL="$tmp/transaction.json"
            resource="$tmp/foreign-object"
            mkdir -p "$resource"
            python3 - "$INSTALL_JOURNAL" <<'PY'
import json
import sys
from pathlib import Path
Path(sys.argv[1]).write_text(json.dumps({"created_resources": []}), encoding="utf-8")
PY

            if foundation_preflight_path_absent_or_owned \
                "$resource" "path:$resource" "Foreign path"; then
                exit 1
            fi
            ''',
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("текущем journal", result.stderr)

    def test_current_transaction_owned_port_is_accepted(self) -> None:
        result = self.run_shell(
            r'''
            set -Eeuo pipefail
            export INSTALLER_FOUNDATION_SOURCE_ONLY=1
            source "$FOUNDATION_PATH"
            unset INSTALLER_FOUNDATION_SOURCE_ONLY
            export INSTALLER_FOUNDATION_COMPAT_SOURCE_ONLY=1
            source "$COMPAT_PATH"
            unset INSTALLER_FOUNDATION_COMPAT_SOURCE_ONLY

            foundation_manifest_has_resource() { return 1; }
            foundation_journal_validate() { return 0; }
            foundation_port_in_use() { return 0; }
            systemctl() { return 0; }

            tmp=$(mktemp -d)
            trap 'rm -rf "$tmp"' EXIT
            INSTALL_JOURNAL="$tmp/transaction.json"
            python3 - "$INSTALL_JOURNAL" <<'PY'
import json
import sys
from pathlib import Path
Path(sys.argv[1]).write_text(
    json.dumps({"created_resources": ["systemd:just1kbot-redis.service"]}),
    encoding="utf-8",
)
PY

            foundation_port_preflight \
                6380 "systemd:just1kbot-redis.service" "Dedicated Redis" "just1kbot-redis.service"
            ''',
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
