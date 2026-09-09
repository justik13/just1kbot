"""Unit tests for White Internet dynamic XHTTP padding."""

import importlib
import json
import os
import unittest
import urllib.parse
from unittest.mock import MagicMock, patch

import config.constants
from database.models import WhiteInternetSubscription
from services.white_internet_service import WhiteInternetService


class TestWhiteInternetXHttpPadding(unittest.TestCase):
    """Test suite for dynamic XHTTP padding parameter propagation in VLESS and Xray configs."""

    def test_canonical_xhttp_profile_padding_default(self):
        """Verify CANONICAL_XHTTP_PROFILE has default xPaddingBytes 100-1000."""
        self.assertEqual(config.constants.CANONICAL_XHTTP_PROFILE.get("xPaddingBytes"), "100-1000")

    def test_canonical_xhttp_profile_padding_env_override(self):
        """Verify WHITE_INTERNET_PADDING_BYTES env var overrides default padding."""
        with patch.dict(os.environ, {"WHITE_INTERNET_PADDING_BYTES": "250-750"}):
            importlib.reload(config.constants)
            self.assertEqual(config.constants.CANONICAL_XHTTP_PROFILE.get("xPaddingBytes"), "250-750")

        # Reload back to restore
        importlib.reload(config.constants)
        self.assertEqual(config.constants.CANONICAL_XHTTP_PROFILE.get("xPaddingBytes"), "100-1000")

    def test_generate_vless_links_contains_padding(self):
        """Verify generate_vless_links includes xPaddingBytes in extra query parameter."""
        sub = MagicMock(spec=WhiteInternetSubscription)
        sub.uuid = "12345678-1234-1234-1234-123456789abc"
        cdn_domain = "cdn.test.local"

        links = WhiteInternetService.generate_vless_links(sub, cdn_domain)
        self.assertTrue(len(links) > 0)

        parsed = urllib.parse.urlparse(links[0])
        qs = urllib.parse.parse_qs(parsed.query)
        self.assertIn("extra", qs)

        extra = json.loads(qs["extra"][0])
        self.assertIn("xPaddingBytes", extra)
        self.assertEqual(extra["xPaddingBytes"], "100-1000")

    def test_generate_full_xray_config_contains_padding(self):
        """Verify generate_full_xray_config propagates xPaddingBytes to xhttpSettings."""
        sub = MagicMock(spec=WhiteInternetSubscription)
        sub.uuid = "12345678-1234-1234-1234-123456789abc"
        cdn_domain = "cdn.test.local"

        cfg = WhiteInternetService.generate_full_xray_config(sub, cdn_domain)
        outbounds = cfg.get("outbounds", [])
        self.assertTrue(len(outbounds) > 0)
        outbound = outbounds[0]

        stream_settings = outbound.get("streamSettings", {})
        self.assertEqual(stream_settings.get("network"), "xhttp")
        xhttp_settings = stream_settings.get("xhttpSettings", {})
        self.assertEqual(xhttp_settings.get("xPaddingBytes"), "100-1000")
