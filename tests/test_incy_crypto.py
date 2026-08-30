"""Tests for INCY crypt1 deep link encryption and decryption."""

import unittest

from services.incy_crypto import (
    EXPECTED_KEY_FINGERPRINT,
    decrypt_link,
    derive_key,
    encrypt_link,
    encrypt_link_deterministic,
)


class TestIncyCrypto(unittest.TestCase):
    def test_key_fingerprint(self):
        """Derived K1 must match the published client key fingerprint."""
        key = derive_key()
        self.assertEqual(len(key), 32)
        import hashlib
        fp = hashlib.sha256(key).hexdigest()
        self.assertEqual(fp, EXPECTED_KEY_FINGERPRINT)

    def test_official_golden_vector(self):
        """Must match the official @incy/link-encoder test vector bit-for-bit."""
        iv = bytes.fromhex("000102030405060708090a0b")
        url = "https://sub.example.com/test-vector"
        expected = (
            "incy://crypt1/AAECAwQFBgcICQoLNyIQL3rDwRZqnyoD8pGKSLXP6o8NdSXQVSSALNbbUyIr"
            "__tWGFUexdIfKvvmDnuDGbmBvuppfNef6aKNZUwOm4c-Sg"
        )
        link = encrypt_link_deterministic(url, iv=iv)
        self.assertEqual(link, expected)

        # Decrypt golden vector
        dec = decrypt_link(expected)
        self.assertEqual(dec["url"], url)
        self.assertIsNone(dec["name"])

    def test_roundtrip_with_name(self):
        """Encryption with custom Cyrillic profile name roundtrips cleanly."""
        url = "https://bot.example.com/sub/wl/test-token-12345"
        name = "Just1k Белый Интернет"
        link = encrypt_link(url, name=name)
        self.assertTrue(link.startswith("incy://crypt1/"))

        dec = decrypt_link(link)
        self.assertEqual(dec["url"], url)
        self.assertEqual(dec["name"], name)

    def test_invalid_urls(self):
        """HTTP, non-absolute, or empty URLs must be rejected."""
        with self.assertRaises(ValueError):
            encrypt_link("http://example.com/sub")
        with self.assertRaises(ValueError):
            encrypt_link("/relative/path")
        with self.assertRaises(ValueError):
            encrypt_link("")

    def test_tampered_payload_rejected(self):
        """Tampered ciphertext must fail authentication."""
        link = encrypt_link("https://example.com/sub")
        tampered = link[:-2] + "AA"
        with self.assertRaises(ValueError):
            decrypt_link(tampered)


if __name__ == "__main__":
    unittest.main()
