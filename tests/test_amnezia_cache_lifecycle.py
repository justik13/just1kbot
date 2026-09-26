import shutil
import tempfile
import unittest
from pathlib import Path


class TestAmneziaCacheLifecycle(unittest.TestCase):
    """
    Tests for amnezia_api cache discovery, validation, and safe recovery logic.
    Addresses audit findings on:
    1. Empty/partial cache directory must not block fallback.
    2. Candidate directory is valid only when both app.py and requirements.txt are present.
    3. Safe recovery moves nested amnezia_api/. and only removes source if app.py is verified.
    """

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.install_dir = Path(self.test_dir) / "opt" / "just1knode"
        self.amnezia_api_dir = Path(self.test_dir) / "opt" / "amnezia-api"
        self.install_dir.mkdir(parents=True, exist_ok=True)
        self.amnezia_api_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _discover_valid_source(self, cand_dirs: list[Path]) -> Path | None:
        """Emulates the bash cand_dirs discovery logic in amnezia.sh."""
        for cand in cand_dirs:
            if (cand / "app.py").is_file() and (cand / "requirements.txt").is_file():
                return cand
        return None

    def _safe_nested_recovery(self, target_dir: Path) -> bool:
        """Emulates the safe nested amnezia_api recovery in amnezia.sh and core.sh."""
        nested = target_dir / "amnezia_api"
        if nested.is_dir() and not (target_dir / "app.py").is_file():
            if (nested / "app.py").is_file():
                for item in nested.iterdir():
                    dest = target_dir / item.name
                    if item.is_dir():
                        shutil.copytree(item, dest, dirs_exist_ok=True)
                    else:
                        shutil.copy2(item, dest)
                if (target_dir / "app.py").is_file():
                    shutil.rmtree(nested)
                    return True
        return False

    def test_empty_cache_dir_is_not_selected_as_valid_source(self):
        """An empty cache directory must be rejected, allowing fallback."""
        empty_cache = self.install_dir / "scripts" / "amnezia_api"
        empty_cache.mkdir(parents=True, exist_ok=True)

        found = self._discover_valid_source([empty_cache])
        self.assertIsNone(found, "Empty cache directory must not be selected as a valid source")

    def test_partial_cache_missing_requirements_is_rejected(self):
        """A cache directory with only app.py but no requirements.txt must be rejected."""
        partial_cache = self.install_dir / "scripts" / "amnezia_api"
        partial_cache.mkdir(parents=True, exist_ok=True)
        (partial_cache / "app.py").write_text("# app.py", encoding="utf-8")

        found = self._discover_valid_source([partial_cache])
        self.assertIsNone(found, "Partial cache missing requirements.txt must not be selected")

    def test_valid_cache_is_selected_and_copied(self):
        """A complete cache directory with app.py and requirements.txt is selected."""
        valid_cache = self.install_dir / "scripts" / "amnezia_api"
        valid_cache.mkdir(parents=True, exist_ok=True)
        (valid_cache / "app.py").write_text("# valid app.py", encoding="utf-8")
        (valid_cache / "requirements.txt").write_text("fastapi==0.141.1\n", encoding="utf-8")

        found = self._discover_valid_source([valid_cache])
        self.assertIsNotNone(found)
        self.assertEqual(found, valid_cache)

        # Simulate copying into AMNEZIA_API_DIR
        for item in found.iterdir():
            shutil.copy2(item, self.amnezia_api_dir / item.name)

        self.assertTrue((self.amnezia_api_dir / "app.py").is_file())
        self.assertTrue((self.amnezia_api_dir / "requirements.txt").is_file())

    def test_safe_nested_recovery_success(self):
        """When nested amnezia_api exists, recovery moves files and removes nested dir."""
        nested = self.amnezia_api_dir / "amnezia_api"
        nested.mkdir(parents=True, exist_ok=True)
        (nested / "app.py").write_text("# nested app.py", encoding="utf-8")
        (nested / "requirements.txt").write_text("fastapi\n", encoding="utf-8")

        self.assertFalse((self.amnezia_api_dir / "app.py").is_file())
        recovered = self._safe_nested_recovery(self.amnezia_api_dir)

        self.assertTrue(recovered)
        self.assertTrue((self.amnezia_api_dir / "app.py").is_file())
        self.assertTrue((self.amnezia_api_dir / "requirements.txt").is_file())
        self.assertFalse(nested.exists(), "Nested directory should be removed after verified copy")

    def test_safe_nested_recovery_noop_if_no_nested_app(self):
        """If nested directory has no app.py, recovery does not run and preserves directory."""
        nested = self.amnezia_api_dir / "amnezia_api"
        nested.mkdir(parents=True, exist_ok=True)
        (nested / "other.txt").write_text("other", encoding="utf-8")

        recovered = self._safe_nested_recovery(self.amnezia_api_dir)
        self.assertFalse(recovered)
        self.assertTrue(nested.exists(), "Source directory must not be removed if no recovery occurred")
