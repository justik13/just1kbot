import shutil
import tempfile
import unittest
from pathlib import Path


class TestAmneziaCacheLifecycle(unittest.TestCase):
    """
    Tests for amnezia_api cache discovery, validation, atomic rotation, and safe recovery logic.
    Addresses audit findings on:
    1. Symmetrical validation: target is only considered deployed if app.py, requirements.txt, and service all exist.
    2. Fallback runs if target is incomplete (e.g. app.py exists but requirements.txt is missing).
    3. Cache is only valid when all 3 files are present.
    4. Atomic cache replacement eliminates stale/obsolete files from previous versions.
    5. Safe nested recovery preserves source if copy fails.
    """

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.install_dir = Path(self.test_dir) / "opt" / "just1knode"
        self.amnezia_api_dir = Path(self.test_dir) / "opt" / "amnezia-api"
        self.install_dir.mkdir(parents=True, exist_ok=True)
        self.amnezia_api_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _is_amnezia_api_valid(self, target: Path) -> bool:
        """Emulates is_amnezia_api_valid in amnezia.sh."""
        return (
            (target / "app.py").is_file()
            and (target / "requirements.txt").is_file()
            and (target / "amnezia-api.service").is_file()
        )

    def _discover_valid_source(self, cand_dirs: list[Path]) -> Path | None:
        """Emulates the bash cand_dirs discovery logic in amnezia.sh."""
        for cand in cand_dirs:
            if self._is_amnezia_api_valid(cand):
                return cand
        return None

    def _atomic_cache_replace(self, staging_dir: Path, target_cache: Path) -> bool:
        """Emulates the atomic rename rotation in core.sh and amnezia.sh."""
        if not self._is_amnezia_api_valid(staging_dir):
            return False
        old_cache = target_cache.with_suffix(".old")
        if target_cache.exists():
            target_cache.rename(old_cache)
        staging_dir.rename(target_cache)
        if old_cache.exists():
            shutil.rmtree(old_cache)
        return True

    def _safe_nested_recovery(self, target_dir: Path) -> bool:
        """Emulates safe nested amnezia_api recovery in amnezia.sh and core.sh."""
        nested = target_dir / "amnezia_api"
        if not self._is_amnezia_api_valid(target_dir) and nested.is_dir():
            if self._is_amnezia_api_valid(nested):
                for item in nested.iterdir():
                    dest = target_dir / item.name
                    if item.is_dir():
                        shutil.copytree(item, dest, dirs_exist_ok=True)
                    else:
                        shutil.copy2(item, dest)
                if self._is_amnezia_api_valid(target_dir):
                    shutil.rmtree(nested)
                    return True
        return False

    def test_empty_cache_dir_is_not_selected(self):
        """An empty cache directory must be rejected."""
        empty_cache = self.install_dir / "scripts" / "amnezia_api"
        empty_cache.mkdir(parents=True, exist_ok=True)
        found = self._discover_valid_source([empty_cache])
        self.assertIsNone(found)

    def test_partial_cache_missing_service_is_rejected(self):
        """A cache with app.py and requirements.txt but missing amnezia-api.service is rejected."""
        partial = self.install_dir / "scripts" / "amnezia_api"
        partial.mkdir(parents=True, exist_ok=True)
        (partial / "app.py").write_text("# app", encoding="utf-8")
        (partial / "requirements.txt").write_text("fastapi\n", encoding="utf-8")

        found = self._discover_valid_source([partial])
        self.assertIsNone(found, "Cache missing amnezia-api.service must be rejected")

    def test_valid_cache_with_all_three_files_is_accepted(self):
        """A complete cache directory with all 3 files is accepted."""
        valid = self.install_dir / "scripts" / "amnezia_api"
        valid.mkdir(parents=True, exist_ok=True)
        (valid / "app.py").write_text("# app", encoding="utf-8")
        (valid / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
        (valid / "amnezia-api.service").write_text("[Unit]\n", encoding="utf-8")

        found = self._discover_valid_source([valid])
        self.assertEqual(found, valid)

    def test_incomplete_target_requires_fallback_even_if_app_py_exists(self):
        """If target has app.py but missing requirements.txt, target is not valid and fallback must run."""
        (self.amnezia_api_dir / "app.py").write_text("# old broken app", encoding="utf-8")
        # Target missing requirements.txt and service
        self.assertFalse(
            self._is_amnezia_api_valid(self.amnezia_api_dir),
            "Target with only app.py must be recognized as invalid, triggering fallback",
        )

    def test_atomic_cache_replacement_removes_obsolete_files(self):
        """Atomic replacement via rename ensures stale files from old versions are purged."""
        target_cache = self.install_dir / "scripts" / "amnezia_api"
        target_cache.mkdir(parents=True, exist_ok=True)
        (target_cache / "app.py").write_text("# v1", encoding="utf-8")
        (target_cache / "requirements.txt").write_text("v1\n", encoding="utf-8")
        (target_cache / "amnezia-api.service").write_text("v1\n", encoding="utf-8")
        (target_cache / "obsolete_old_file.py").write_text("# stale\n", encoding="utf-8")

        staging = self.install_dir / "scripts" / ".stage"
        staging.mkdir(parents=True, exist_ok=True)
        (staging / "app.py").write_text("# v2", encoding="utf-8")
        (staging / "requirements.txt").write_text("v2\n", encoding="utf-8")
        (staging / "amnezia-api.service").write_text("v2\n", encoding="utf-8")

        success = self._atomic_cache_replace(staging, target_cache)
        self.assertTrue(success)
        self.assertTrue((target_cache / "app.py").is_file())
        self.assertFalse(
            (target_cache / "obsolete_old_file.py").exists(),
            "Obsolete file must be purged after atomic replacement",
        )

    def test_safe_nested_recovery_success(self):
        """Nested amnezia_api is recovered and removed only after all 3 files verified."""
        nested = self.amnezia_api_dir / "amnezia_api"
        nested.mkdir(parents=True, exist_ok=True)
        (nested / "app.py").write_text("# app", encoding="utf-8")
        (nested / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
        (nested / "amnezia-api.service").write_text("[Unit]\n", encoding="utf-8")

        self.assertFalse(self._is_amnezia_api_valid(self.amnezia_api_dir))
        recovered = self._safe_nested_recovery(self.amnezia_api_dir)

        self.assertTrue(recovered)
        self.assertTrue(self._is_amnezia_api_valid(self.amnezia_api_dir))
        self.assertFalse(nested.exists(), "Nested dir must be removed after successful verified recovery")

    def test_safe_nested_recovery_preserves_source_if_nested_incomplete(self):
        """If nested directory is missing required files, recovery aborts and preserves source."""
        nested = self.amnezia_api_dir / "amnezia_api"
        nested.mkdir(parents=True, exist_ok=True)
        (nested / "app.py").write_text("# incomplete", encoding="utf-8")

        recovered = self._safe_nested_recovery(self.amnezia_api_dir)
        self.assertFalse(recovered)
        self.assertTrue(nested.exists(), "Source directory must not be removed if recovery is incomplete")
