import os
import pathlib
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


class ShellLayoutTests(unittest.TestCase):
    def test_root_has_only_interactive_shell_entrypoint(self):
        root_scripts = sorted(path.name for path in ROOT.glob("*.sh"))
        self.assertEqual(root_scripts, ["deploy.sh"])

    def test_required_scripts_are_under_scripts_directory(self):
        required = {
            "deploy.sh",
            "deploy_full.sh",
            "update_from_github.sh",
            "setup-amnezia-api.sh",
            "uninstall.sh",
            "lib/postgresql.sh",
            "ops/deploy_application.sh",
            "ops/backup_postgres.sh",
            "ops/verify_backup.sh",
            "ops/restore_rehearsal.sh",
            "ops/just1kbot-restore.sh",
        }
        missing = sorted(name for name in required if not (SCRIPTS / name).is_file())
        self.assertEqual(missing, [])

    def test_all_repository_shell_scripts_parse(self):
        scripts = sorted(ROOT.rglob("*.sh"))
        self.assertTrue(scripts)
        for script in scripts:
            with self.subTest(script=script.relative_to(ROOT)):
                subprocess.run(["bash", "-n", str(script)], check=True)

    def test_menu_help_is_non_destructive(self):
        result = subprocess.run(
            ["bash", str(ROOT / "deploy.sh"), "help"],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Just1kBot", result.stdout)
        self.assertIn("restore-test", result.stdout)
        self.assertIn("update", result.stdout)

    def test_postgresql_port_repair_changes_only_database_url_port(self):
        with tempfile.TemporaryDirectory() as directory:
            env_file = pathlib.Path(directory) / ".env"
            env_file.write_text(
                'BOT_TOKEN="keep"\n'
                'DATABASE_URL="postgresql+asyncpg://just1kbot:secret@127.0.0.1:5432/just1kbot_bot"\n'
                'REDIS_URL="redis://keep"\n',
                encoding="utf-8",
            )
            env_file.chmod(0o640)
            before = env_file.stat()

            command = f"""
set -Eeuo pipefail
ENV_FILE={str(env_file)!r}
source {str(SCRIPTS / 'lib/postgresql.sh')!r}
PG_PORT=5433
pg_repair_env_port
"""
            result = subprocess.run(
                ["bash", "-c", command],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            self.assertEqual(
                env_file.read_text(encoding="utf-8"),
                'BOT_TOKEN="keep"\n'
                'DATABASE_URL="postgresql+asyncpg://just1kbot:secret@127.0.0.1:5433/just1kbot_bot"\n'
                'REDIS_URL="redis://keep"\n',
            )
            after = env_file.stat()
            self.assertEqual(before.st_mode & 0o777, after.st_mode & 0o777)
            self.assertEqual(before.st_uid, after.st_uid)
            self.assertEqual(before.st_gid, after.st_gid)

    def test_first_install_rollback_does_not_start_absent_service(self):
        adapter = (SCRIPTS / "deploy.sh").read_text(encoding="utf-8")
        self.assertIn('previous_service=absent start_not_attempted=true', adapter)
        absent_branch = adapter.index('if [[ ! -f "$ROLLBACK_SNAPSHOT/systemd.service" ]]')
        next_start = adapter.index("service_call start", absent_branch)
        branch_return = adapter.index("return 1", absent_branch)
        self.assertLess(branch_return, next_start)


if __name__ == "__main__":
    unittest.main()
