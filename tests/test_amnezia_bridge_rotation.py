import unittest

from services.amnezia_bridge_token_service import AmneziaBridgeTokenService

SECRET_A = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
SECRET_B = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


class AmneziaBridgeRotationTests(unittest.TestCase):
    def test_key_rotation_invalidates_previous_signatures(self):
        profile_id = 55
        user_id = 77
        exp = 1800000000

        # Link signed with key A
        sig_a = AmneziaBridgeTokenService.sign(profile_id, user_id, exp, secret=SECRET_A)
        self.assertTrue(AmneziaBridgeTokenService.verify(profile_id, user_id, exp, sig_a, secret=SECRET_A))

        # Verification with rotated key B MUST fail
        self.assertFalse(AmneziaBridgeTokenService.verify(profile_id, user_id, exp, sig_a, secret=SECRET_B))

        # New link signed with key B works with key B
        sig_b = AmneziaBridgeTokenService.sign(profile_id, user_id, exp, secret=SECRET_B)
        self.assertTrue(AmneziaBridgeTokenService.verify(profile_id, user_id, exp, sig_b, secret=SECRET_B))

        # But fails with old key A
        self.assertFalse(AmneziaBridgeTokenService.verify(profile_id, user_id, exp, sig_b, secret=SECRET_A))


if __name__ == "__main__":
    unittest.main()
