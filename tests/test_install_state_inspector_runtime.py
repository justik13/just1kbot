import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSPECTOR = ROOT / "scripts" / "inspect_install_state.sh"


class InstallStateInspectorRuntimeTests(unittest.TestCase):
    def make_env(self, root: Path) -> dict[str, str]:
        project = root / "opt" / "just1kbot"
        state_root = root / "var" / "lib" / "just1kbot"
        state_dir = state_root / "install-state"
        env = os.environ.copy()
        env.update(
            {
                "PROJECT_DIR": str(project),
                "ENV_FILE": str(project / ".env"),
                "UNIT_FILE": str(root / "etc" / "systemd" / "just1kbot.service"),
                "BOT_USER": "just1kbot-inspector-test-user-does-not-exist",
                "BOT_HOME": str(root / "home" / "just1kbot"),
                "CLI_SBIN": str(root / "usr" / "local" / "sbin" / "just1kbot"),
                "CLI_BIN": str(root / "usr" / "local" / "bin" / "just1kbot"),
                "STATE_ROOT": str(state_root),
                "STATE_DIR": str(state_dir),
                "MANIFEST": str(state_dir / "manifest.json"),
                "TRANSACTION": str(state_dir / "transaction.json"),
                "BACKUP_DIR": str(root / "backups" / "just1kbot"),
                "BACKUP_CONF": str(root / "etc" / "just1kbot-backup.conf"),
                "BACKUP_IDENTITY": str(root / "root" / ".config" / "just1kbot" / "backup.agekey"),
                "BACKUP_TOOL": str(root / "usr" / "local" / "bin" / "just1kbot-backup.sh"),
                "RESTORE_TOOL": str(root / "usr" / "local" / "bin" / "just1kbot-restore.sh"),
                "HEALTHCHECK_TOOL": str(root / "usr" / "local" / "bin" / "just1kbot-healthcheck.sh"),
                "VERIFY_TOOL": str(root / "usr" / "local" / "bin" / "verify_backup.sh"),
                "REHEARSAL_TOOL": str(root / "usr" / "local" / "bin" / "restore_rehearsal.sh"),
                "INSTALL_STATE_SKIP_USER_LOOKUP": "1",
            }
        )
        return env

    def run_inspector(
        self,
        env: dict[str, str],
        *,
        operation: str = "deploy",
        require_safe: bool = True,
    ) -> tuple[subprocess.CompletedProcess[str], dict[str, object]]:
        command = [
            "bash",
            str(INSPECTOR),
            "--json",
            "--operation",
            operation,
        ]
        if require_safe:
            command.append("--require-safe")
        result = subprocess.run(
            command,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        payload = json.loads(result.stdout)
        return result, payload

    @staticmethod
    def create_legacy_project(env: dict[str, str]) -> Path:
        project = Path(env["PROJECT_DIR"])
        (project / "scripts").mkdir(parents=True)
        (project / "deploy.sh").write_text(
            "#!/bin/bash\n# Just1kBot control plane\n",
            encoding="utf-8",
        )
        (project / "scripts" / "deploy.sh").write_text(
            "#!/bin/bash\n",
            encoding="utf-8",
        )
        return project

    def create_legacy_cli(self, env: dict[str, str]) -> Path:
        cli = Path(env["CLI_SBIN"])
        cli.parent.mkdir(parents=True)
        cli.write_text(
            "#!/bin/bash\n"
            "# Just1kBot root control plane legacy launcher\n"
            "PROJECT=/opt/just1kbot\n",
            encoding="utf-8",
        )
        return cli

    def test_clean_state_is_safe_for_deploy(self):
        with tempfile.TemporaryDirectory() as directory:
            env = self.make_env(Path(directory))
            result, payload = self.run_inspector(env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload["state"], "clean")
        self.assertEqual(payload["operation"], "deploy")
        self.assertEqual(payload["evidence"], [])

    def test_orphaned_legacy_cli_is_residual_not_clean(self):
        with tempfile.TemporaryDirectory() as directory:
            env = self.make_env(Path(directory))
            cli = self.create_legacy_cli(env)
            result, payload = self.run_inspector(env)
        self.assertEqual(result.returncode, 22, result.stderr)
        self.assertEqual(payload["state"], "residual_managed")
        self.assertIn(str(cli), str(payload["reason"]))
        self.assertIn("reset_legacy_install.sh", str(payload["action"]))

    def test_symlink_collision_is_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = self.make_env(root)
            target = root / "foreign"
            target.mkdir()
            project = Path(env["PROJECT_DIR"])
            project.parent.mkdir(parents=True)
            project.symlink_to(target, target_is_directory=True)
            result, payload = self.run_inspector(env)
        self.assertEqual(result.returncode, 20)
        self.assertEqual(payload["state"], "foreign_collision")
        self.assertIn("symlink", str(payload["reason"]))

    def test_foreign_unit_is_not_accepted_by_name_only(self):
        with tempfile.TemporaryDirectory() as directory:
            env = self.make_env(Path(directory))
            unit = Path(env["UNIT_FILE"])
            unit.parent.mkdir(parents=True)
            unit.write_text(
                "[Unit]\nDescription=Unrelated service\n"
                "[Service]\nExecStart=/usr/bin/false\n",
                encoding="utf-8",
            )
            result, payload = self.run_inspector(env)
        self.assertEqual(result.returncode, 20)
        self.assertEqual(payload["state"], "foreign_collision")
        self.assertIn("unit", str(payload["reason"]))

    def test_known_incomplete_install_requires_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            env = self.make_env(Path(directory))
            project = self.create_legacy_project(env)
            (project / ".env").write_text('BOT_TOKEN="redacted"\n', encoding="utf-8")
            result, payload = self.run_inspector(env)
        self.assertEqual(result.returncode, 23, result.stderr)
        self.assertEqual(payload["state"], "partial_install")
        self.assertIn("recovery", str(payload["action"]).lower())

    def test_confirmed_preserved_backup_blocks_deploy_but_allows_uninstall(self):
        with tempfile.TemporaryDirectory() as directory:
            env = self.make_env(Path(directory))
            backup_dir = Path(env["BACKUP_DIR"])
            backup_dir.mkdir(parents=True)
            (backup_dir / "just1kbot-pg-v1-20260803T000000Z.tar.age").write_bytes(b"backup")
            backup_conf = Path(env["BACKUP_CONF"])
            backup_conf.parent.mkdir(parents=True, exist_ok=True)
            backup_conf.write_text(
                "BACKUP_AGE_RECIPIENT=age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq\n",
                encoding="utf-8",
            )

            deploy_result, deploy_payload = self.run_inspector(env, operation="deploy")
            uninstall_result, uninstall_payload = self.run_inspector(env, operation="uninstall")

        self.assertEqual(deploy_result.returncode, 22)
        self.assertEqual(deploy_payload["state"], "residual_managed")
        self.assertEqual(uninstall_result.returncode, 0, uninstall_result.stderr)
        self.assertEqual(uninstall_payload["state"], "residual_managed")

    def test_corrupted_manifest_blocks_every_operation(self):
        with tempfile.TemporaryDirectory() as directory:
            env = self.make_env(Path(directory))
            manifest = Path(env["MANIFEST"])
            manifest.parent.mkdir(parents=True)
            manifest.write_text("{not-json", encoding="utf-8")
            deploy_result, deploy_payload = self.run_inspector(env, operation="deploy")
            uninstall_result, uninstall_payload = self.run_inspector(env, operation="uninstall")

        self.assertEqual(deploy_result.returncode, 21)
        self.assertEqual(uninstall_result.returncode, 21)
        self.assertEqual(deploy_payload["state"], "corrupted_state")
        self.assertEqual(uninstall_payload["state"], "corrupted_state")


if __name__ == "__main__":
    unittest.main()
