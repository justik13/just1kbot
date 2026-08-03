import pathlib
import subprocess
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
UPDATER = ROOT / "scripts" / "update_from_github.sh"
CONTROL_PLANE = ROOT / "scripts" / "lib" / "control_plane.sh"


class GithubUpdateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.updater = UPDATER.read_text(encoding="utf-8")
        cls.control = CONTROL_PLANE.read_text(encoding="utf-8")

    def test_help_is_non_destructive_and_available_without_root(self):
        result = subprocess.run(
            ["bash", str(UPDATER), "--help"],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("https://github.com/justik13/projectx", result.stdout)
        self.assertIn("--check", result.stdout)
        self.assertIn("--dry-run", result.stdout)

    def test_unknown_argument_fails_before_any_update(self):
        result = subprocess.run(
            ["bash", str(UPDATER), "--wrong-option"],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("Неизвестный аргумент update", result.stderr)

    def test_source_and_ref_are_fixed_and_live_is_not_a_checkout(self):
        for marker in (
            "readonly REPOSITORY_URL='https://github.com/justik13/projectx.git'",
            "readonly REPOSITORY_REF='refs/heads/main'",
            "readonly RELEASE_ROOT='/var/lib/just1kbot/source-releases'",
            "readonly LIVE_DIR='/opt/just1kbot'",
        ):
            self.assertIn(marker, self.updater)
        self.assertNotIn("git pull", self.updater)
        self.assertNotIn('git -C "$LIVE_DIR"', self.updater)
        self.assertNotIn("--repository", self.updater)
        self.assertNotIn("--branch", self.updater)

    def test_git_fetch_is_sanitized_bounded_and_commit_is_pinned(self):
        for marker in (
            "readonly GIT_TIMEOUT_SECONDS=120",
            'timeout --foreground "$GIT_TIMEOUT_SECONDS"',
            "env -i",
            "GIT_TERMINAL_PROMPT=0",
            "GIT_CONFIG_NOSYSTEM=1",
            "GIT_CONFIG_GLOBAL=/dev/null",
            "core.hooksPath=/dev/null",
            "protocol.file.allow=never",
            "protocol.ext.allow=never",
            'fetch --quiet --depth=1 --no-tags origin "$REPOSITORY_REF"',
            "FETCH_HEAD^{commit}",
            'checkout --quiet --detach --force "$TARGET_SHA"',
            "fsck --strict --no-dangling",
        ):
            self.assertIn(marker, self.updater)

    def test_installed_sha_requires_trusted_metadata(self):
        for marker in (
            "source_repository=$REPOSITORY_URL",
            "source_ref=$REPOSITORY_REF",
            "source_commit=$TARGET_SHA",
            "stat -c '%u'",
            "(8#$mode & 8#022) == 0",
            'grep -Fxq "source_repository=$REPOSITORY_URL"',
            'grep -Fxq "source_ref=$REPOSITORY_REF"',
        ):
            self.assertIn(marker, self.updater)

    def test_checkout_rejects_active_content_features(self):
        for marker in (
            'mode in {b"120000", b"160000"}',
            ".gitmodules",
            "repository содержит symlink",
            "control character in tracked path",
            "scripts/ops/deploy_application.sh",
        ):
            self.assertIn(marker, self.updater)

    def test_release_is_hardened_and_records_exact_commit(self):
        for marker in (
            "source_commit=$TARGET_SHA",
            "chown -R root:root",
            "find \"$TEMP_RELEASE\" -xdev -type d -exec chmod 0700",
            "find \"$TEMP_RELEASE\" -xdev -type f ! -perm /111 -exec chmod 0600",
            "release-${stamp}-${TARGET_SHA:0:12}",
        ):
            self.assertIn(marker, self.updater)

    def test_update_delegates_to_transactional_deploy(self):
        for marker in (
            'JUST1KBOT_SOURCE_COMMIT="$TARGET_SHA"',
            'bash "$PUBLISHED_RELEASE/deploy.sh" "${arguments[@]}"',
            "local -a arguments=(deploy)",
            "Source release сохранён",
        ):
            self.assertIn(marker, self.updater)

    def test_control_plane_exposes_update_without_nested_lock(self):
        update_case = self.control[
            self.control.index("        update)") : self.control.index("        deploy)")
        ]
        self.assertIn('call_script update_from_github.sh "$@"', update_case)
        self.assertIn("smoke", update_case)
        self.assertNotIn("run_locked_script", update_case)
        self.assertIn("Update from GitHub", self.control)
        self.assertIn("sudo bash deploy.sh update", self.control)


if __name__ == "__main__":
    unittest.main()
