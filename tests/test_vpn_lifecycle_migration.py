import unittest
from pathlib import Path


class CleanBaselineMigrationTests(unittest.TestCase):
    def test_baseline_contains_final_vpn_lifecycle_without_legacy_backfill(self):
        source = (
            Path(__file__).parents[1]
            / "alembic"
            / "versions"
            / "0001_clean_baseline.py"
        ).read_text(encoding="utf-8")
        self.assertIn("CREATE TABLE public.vpn_profiles", source)
        self.assertIn("ck_vpn_profiles_provisioning_status", source)
        self.assertNotIn("Cannot downgrade VPN lifecycle", source)
        self.assertNotIn("DELETE FROM vpn_profiles WHERE peer_id IS NULL", source)


if __name__ == "__main__":
    unittest.main()
