"""Automated DOM contract and JS selector verification for Amnezia Web Bridge."""

import re
import unittest

from integrations.amnezia_bridge.web_templates import (
    render_500_html,
    render_amnezia_bridge_html,
    render_error_html,
    render_expired_html,
)


class AmneziaBridgeDomContractTests(unittest.TestCase):
    """Verify rendered HTML and embedded JS adhere strictly to DOM ID contracts."""

    def test_bridge_render_contains_vpn_key_and_uri(self):
        """Rendered HTML must contain the textarea with id='vpn-key' containing the exact URI."""
        test_uri = "vpn://connect-test-key-12345"
        html_doc = render_amnezia_bridge_html(
            vpn_uri=test_uri,
            server_name="Test-NL-1",
            device_name="My Phone",
            country_flag="🇳🇱",
        )

        self.assertIn('id="vpn-key"', html_doc, "HTML must contain element with id='vpn-key'")
        self.assertIn(test_uri, html_doc, "HTML textarea must contain the escaped vpn_uri")

        # Verify textarea has expected id and hidden attribute
        textarea_match = re.search(r'<textarea\s+id="vpn-key"[^>]*>(.*?)</textarea>', html_doc, re.DOTALL)
        self.assertIsNotNone(textarea_match, "Must find <textarea id='vpn-key'>...</textarea>")
        self.assertEqual(textarea_match.group(1), test_uri)

    def test_bridge_js_dom_contract_selectors_exist_in_html(self):
        """Every document.getElementById('X') referenced in embedded JS must exist as id='X' in HTML."""
        html_doc = render_amnezia_bridge_html(
            vpn_uri="vpn://test-uri",
            server_name="Server-1",
            device_name="Device-1",
        )

        # Extract JS code from <script>...</script>
        script_match = re.search(r"<script>(.*?)</script>", html_doc, re.DOTALL)
        self.assertIsNotNone(script_match, "Rendered HTML must contain embedded <script>")
        js_code = script_match.group(1)

        # Find all getElementById calls
        referenced_ids = re.findall(r'document\.getElementById\(["\']([^"\']+)["\']\)', js_code)
        self.assertGreater(len(referenced_ids), 0, "JS must reference DOM IDs")

        # Extract all HTML tag IDs (excluding script contents)
        html_without_script = re.sub(r"<script>.*?</script>", "", html_doc, flags=re.DOTALL)
        defined_ids = set(re.findall(r'\bid=["\']([^"\']+)["\']', html_without_script))

        for dom_id in referenced_ids:
            self.assertIn(
                dom_id,
                defined_ids,
                f"JS selector references id={dom_id!r}, but it does not exist in rendered HTML!",
            )

        # Strict check for vpn-key specifically
        self.assertIn("vpn-key", referenced_ids, "JS must reference 'vpn-key'")
        self.assertNotIn("устройство-key", js_code, "JS must never reference mojibake/translated 'устройство-key'")

    def test_rendered_html_has_no_duplicate_dom_ids(self):
        """All rendered HTML templates must have strictly unique DOM IDs."""
        renderers = [
            ("bridge", render_amnezia_bridge_html("vpn://test", "S", "D")),
            ("expired", render_expired_html()),
            ("error", render_error_html("Access Denied", "Token expired")),
            ("500", render_500_html()),
        ]

        for name, html_doc in renderers:
            with self.subTest(template=name):
                all_ids = re.findall(r'\bid=["\']([^"\']+)["\']', html_doc)
                duplicates = [dom_id for dom_id in set(all_ids) if all_ids.count(dom_id) > 1]
                self.assertEqual(
                    duplicates,
                    [],
                    f"Template {name!r} contains duplicate DOM IDs: {duplicates}",
                )

    def test_bridge_critical_handlers_exist(self):
        """Embedded JS must contain all required lifecycle and interaction functions."""
        html_doc = render_amnezia_bridge_html(
            vpn_uri="vpn://test-uri",
            server_name="Server-1",
            device_name="Device-1",
        )

        critical_functions = ["getVpnUri", "openVpnApp", "copyVpnKey", "fallbackCopy"]
        for fn_name in critical_functions:
            self.assertIn(
                f"function {fn_name}",
                html_doc,
                f"Required critical JS function '{fn_name}' missing from bridge template",
            )
