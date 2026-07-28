import unittest
from pathlib import Path

class VPNLifecycleDowngradeTests(unittest.TestCase):
    def test_downgrade_refuses_without_deleting_pending_profile(self):
        source=(Path(__file__).parents[1]/"alembic/versions/d74b3e921c10_vpn_profile_lifecycle.py").read_text()
        self.assertNotIn("DELETE FROM vpn_profiles WHERE peer_id IS NULL",source)
        self.assertIn("Cannot downgrade VPN lifecycle while pending profiles",source)
