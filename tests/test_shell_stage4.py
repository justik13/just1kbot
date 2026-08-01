import os
import pathlib
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
DEPLOY = SCRIPTS / "deploy.sh"
OPERATIONAL = SCRIPTS / "lib" / "operational_transaction.sh"


class ShellStage4Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.deploy = DEPLOY.read_text(encoding="utf-8")
        cls.operational = OPERATIONAL.read_text(encoding="utf-8")

    def test_root_ops_duplicate_is_removed(self):
        self.assertFalse((ROOT / "ops").exists())
        for name in (
            "deploy_application.sh",
            "backup_postgres.sh",
            "verify_backup.sh",
            "restore_rehearsal.sh",
            "just1kbot-restore.sh",
        ):
            self.assertTrue((SCRIPTS / "ops" / name).is_file())

    def test_operational_mutations_run_only_in_activation(self):
        for marker in (
            "ACTIVATION_COMMAND=(activate_release_bundle)",
            "BACKUP_COMMAND=(pause_and_create_pre_migration_backup)",
            "BACKUP_COMMAND=(pause_operational_timers)",
            "install_operational_transaction_overrides",
        ):
            self.assertIn(marker, self.deploy)

        activation = self.deploy[
            self.deploy.index("activate_release_bundle()") :
            self.deploy.index("pause_and_create_pre_migration_backup()")
        ]
        for marker in (
            "install_backup_tooling",
            "install_healthcheck",
            "setup_logrotate",
            "setup_nginx_initial",
            "refresh_configured_nginx",
            "setup_systemd",
        ):
            self.assertIn(marker, activation)

        run_deploy = self.deploy[self.deploy.index("run_deploy()") :]
        transaction = run_deploy.index("run_application_transaction")
        for marker in ("install_backup_tooling", "install_healthcheck", "setup_logrotate"):
            self.assertNotIn(marker, run_deploy[:transaction])
        self.assertNotIn("resume_operational_timers || true", run_deploy)

    def test_snapshot_tracks_present_absent_files_and_unit_state(self):
        for marker in (
            "files.tsv",
            "units.tsv",
            "state=present",
            "state=absent",
            "systemctl is-enabled",
            "systemctl is-active",
            "restore_operational_files",
            "restore_operational_units",
            "reload_restored_nginx",
            "normalize_domain",
            "ensure_operational_parent",
            ".incomplete-operational-",
            "systemctl enable --runtime",
            "systemctl mask --runtime",
        ):
            self.assertIn(marker, self.operational)

    def test_operational_file_snapshot_restores_exact_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            existing = root / "existing"
            absent = root / "absent"
            link = root / "link"
            snapshot = root / "snapshot"
            old_target = root / "old-target"
            new_target = root / "new-target"

            existing.write_text("old\n", encoding="utf-8")
            old_target.write_text("old-target\n", encoding="utf-8")
            new_target.write_text("new-target\n", encoding="utf-8")
            link.symlink_to(old_target)
            snapshot.mkdir()

            command = f"""
set -Eeuo pipefail
deploy_log() {{ :; }}
source {str(OPERATIONAL)!r}
OPERATIONAL_PATHS=({str(existing)!r} {str(absent)!r} {str(link)!r})
snapshot_operational_files {str(snapshot)!r}
printf 'new\\n' > {str(existing)!r}
printf 'created\\n' > {str(absent)!r}
rm -f -- {str(link)!r}
ln -s -- {str(new_target)!r} {str(link)!r}
restore_operational_files {str(snapshot)!r}
[[ $(cat {str(existing)!r}) == old ]]
[[ ! -e {str(absent)!r} && ! -L {str(absent)!r} ]]
[[ -L {str(link)!r} ]]
[[ $(readlink {str(link)!r}) == {str(old_target)!r} ]]
"""
            result = subprocess.run(
                ["bash", "-c", command],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_operational_snapshot_rejects_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            tracked = root / "tracked-directory"
            snapshot = root / "snapshot"
            tracked.mkdir()
            snapshot.mkdir()
            command = f"""
set -Eeuo pipefail
deploy_log() {{ :; }}
source {str(OPERATIONAL)!r}
OPERATIONAL_PATHS=({str(tracked)!r})
if snapshot_operational_files {str(snapshot)!r}; then
    exit 9
fi
"""
            result = subprocess.run(
                ["bash", "-c", command],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_operational_unit_restore_preserves_runtime_and_mask_states(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            snapshot = root / "snapshot"
            operational = snapshot / "operational"
            operational.mkdir(parents=True)
            calls = root / "calls"
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
            (operational / "units.tsv").write_text(
                "enabled\tactive\talpha.timer\n"
                "enabled-runtime\tinactive\tbeta.timer\n"
                "masked\tinactive\tgamma.service\n"
                "masked-runtime\tactive\tdelta.service\n"
                "disabled\tinactive\tepsilon.service\n",
                encoding="utf-8",
            )

            command = f"""
set -Eeuo pipefail
deploy_log() {{ :; }}
source {str(OPERATIONAL)!r}
OPERATIONAL_NGINX=false
restore_operational_units {str(snapshot)!r}
"""
            env = os.environ | {
                "PATH": f"{binary}:{os.environ['PATH']}",
                "CALLS": str(calls),
            }
            result = subprocess.run(
                ["bash", "-c", command],
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            lines = calls.read_text(encoding="utf-8").splitlines()
            self.assertIn("enable alpha.timer", lines)
            self.assertIn("enable --runtime beta.timer", lines)
            self.assertIn("start alpha.timer", lines)
            self.assertIn("start delta.service", lines)
            self.assertIn("mask gamma.service", lines)
            self.assertIn("mask --runtime delta.service", lines)
            self.assertLess(
                lines.index("start delta.service"),
                lines.index("mask --runtime delta.service"),
            )
            for unit in ("beta.timer", "gamma.service", "epsilon.service"):
                self.assertNotIn(f"start {unit}", lines)
            first_start = min(i for i, line in enumerate(lines) if line.startswith("start "))
            last_stop = max(i for i, line in enumerate(lines) if line.startswith("stop "))
            self.assertLess(last_stop, first_start)


if __name__ == "__main__":
    unittest.main()
