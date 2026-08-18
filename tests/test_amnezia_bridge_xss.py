import unittest

from bot.handlers.amnezia_web_templates import (
    render_amnezia_bridge_html,
    render_error_html,
)


class AmneziaBridgeXssTests(unittest.TestCase):
    def test_textarea_xss_vectors_are_escaped(self):
        malicious_key = "vpn://payload</textarea><script>alert('xss')</script>"
        html = render_amnezia_bridge_html(
            vpn_uri=malicious_key,
            server_name="Test Server",
            device_name="Test Device",
            country_flag="🇩🇪",
        )

        self.assertNotIn("<script>alert('xss')</script>", html)
        self.assertIn("&lt;/textarea&gt;&lt;script&gt;alert(&#x27;xss&#x27;)&lt;/script&gt;", html)

    def test_image_xss_vectors_are_escaped(self):
        malicious_key = 'vpn://payload</textarea><img src=x onerror="alert(1)">'
        html = render_amnezia_bridge_html(
            vpn_uri=malicious_key,
            server_name="Server<script>",
            device_name="Device\"onclick=alert(1)",
            country_flag="<svg/onload=alert(1)>",
        )

        self.assertNotIn("Server<script>", html)
        self.assertNotIn('<img src=x onerror="alert(1)">', html)
        self.assertNotIn("<svg/onload=alert(1)>", html)
        self.assertIn("Server&lt;script&gt;", html)
        self.assertIn("&lt;/textarea&gt;&lt;img src=x onerror=&quot;alert(1)&quot;&gt;", html)

    def test_error_template_xss_escaped(self):
        html = render_error_html(
            title="Error <script>alert(1)</script>",
            message="Message <img src=x onerror=alert(1)>",
        )
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertNotIn("<img src=x", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", html)


if __name__ == "__main__":
    unittest.main()
