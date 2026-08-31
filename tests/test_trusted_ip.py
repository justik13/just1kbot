import unittest
from types import SimpleNamespace

from utils.http_rate_limiter import get_trusted_client_ip

TRUSTED = "127.0.0.1,::1,10.0.0.0/8,172.16.0.0/12"


def _request(remote, headers=None, trusted=TRUSTED):
    return SimpleNamespace(
        remote=remote,
        headers=dict(headers or {}),
        app={"trusted_proxies": trusted},
    )


class TrustedClientIpTests(unittest.TestCase):
    def test_untrusted_peer_ignores_forwarding_headers(self):
        request = _request(
            "203.0.113.9",
            headers={
                "X-Real-IP": "185.71.76.11",
                "X-Forwarded-For": "185.71.76.11",
            },
        )
        self.assertEqual(get_trusted_client_ip(request), "203.0.113.9")

    def test_x_real_ip_preferred_from_trusted_proxy(self):
        request = _request(
            "10.0.0.5",
            headers={"X-Real-IP": "198.51.100.7"},
        )
        self.assertEqual(get_trusted_client_ip(request), "198.51.100.7")

    def test_xff_rightmost_untrusted_wins(self):
        request = _request(
            "10.0.0.5",
            headers={"X-Forwarded-For": "198.51.100.7, 10.0.0.9"},
        )
        # 10.0.0.9 is the trusted hop appended by our direct peer; the real
        # client address sits to its left.
        self.assertEqual(get_trusted_client_ip(request), "198.51.100.7")

    def test_xff_spoofed_leftmost_entry_is_ignored(self):
        request = _request(
            "10.0.0.5",
            headers={
                "X-Forwarded-For": "185.71.76.11, 198.51.100.7, 10.0.0.9",
            },
        )
        # The attacker-controlled first entry must never be selected.
        self.assertEqual(get_trusted_client_ip(request), "198.51.100.7")

    def test_xff_all_entries_trusted_falls_back_to_leftmost(self):
        request = _request(
            "10.0.0.5",
            headers={"X-Forwarded-For": "10.0.0.9, 10.0.0.10"},
        )
        # When every entry is itself a trusted proxy the chain has no
        # untrusted client hop; the leftmost valid entry is returned
        # (upstream #229 semantics).
        self.assertEqual(get_trusted_client_ip(request), "10.0.0.9")

    def test_xff_malformed_entries_are_skipped(self):
        request = _request(
            "10.0.0.5",
            headers={"X-Forwarded-For": "not-an-ip, 198.51.100.7"},
        )
        self.assertEqual(get_trusted_client_ip(request), "198.51.100.7")

    def test_no_forwarding_headers_returns_peer(self):
        request = _request("10.0.0.5")
        self.assertEqual(get_trusted_client_ip(request), "10.0.0.5")


if __name__ == "__main__":
    unittest.main()
