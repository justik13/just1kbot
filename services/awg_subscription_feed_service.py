"""Service for generating AmneziaWG subscription feeds compatible with INCY (iOS/Android)."""

from __future__ import annotations

import base64
import logging
from typing import Sequence, Tuple

logger = logging.getLogger(__name__)


class AWGSubscriptionFeedService:
    """Generates subscription feeds with multi-server AmneziaWG profiles for INCY."""

    @staticmethod
    def encode_config_to_awg_uri(conf: str, server_name: str, country_flag: str = "") -> str:
        """Encode a raw WireGuard/AmneziaWG .conf text into an awg:// URI.

        INCY specification requires:
        - Schema: 'awg://' or 'amneziawg://'
        - Payload: URL-safe base64 ('-' and '_' instead of '+' and '/')
        - Fragment: '#<display name>' (optional prefix with flag)
        """
        if not conf or not conf.strip():
            raise ValueError("Configuration text cannot be empty")

        clean_conf = conf.strip()
        b64_conf = base64.urlsafe_b64encode(clean_conf.encode("utf-8")).decode("ascii")

        clean_name = (server_name or "Server").strip()
        clean_flag = (country_flag or "").strip()
        fragment = f"{clean_flag} {clean_name}".strip() if clean_flag else clean_name

        return f"awg://{b64_conf}#{fragment}"

    @classmethod
    def build_subscription_body(cls, server_configs: Sequence[Tuple[str, str, str]]) -> str:
        """Build the Base64-encoded subscription feed body containing multiple servers.

        Args:
            server_configs: Sequence of tuples (conf_text, server_name, country_flag).

        Returns:
            Base64-encoded plain text containing one awg:// URI per line.
        """
        if not server_configs:
            return ""

        lines = []
        for item in server_configs:
            conf, name, flag = item
            try:
                uri = cls.encode_config_to_awg_uri(conf, name, flag)
                lines.append(uri)
            except Exception as exc:
                logger.warning("Failed to encode config for server %s: %s", name, exc)
                continue

        if not lines:
            return ""

        raw_payload = "\n".join(lines)
        return base64.b64encode(raw_payload.encode("utf-8")).decode("ascii")

    @staticmethod
    def build_subscription_headers(
        *,
        profile_title: str = "JUST1K VPN",
        expire_ts: int = 0,
        upload_bytes: int = 0,
        download_bytes: int = 0,
        total_quota_bytes: int = 0,
        update_interval_hours: int = 6,
        support_url: str | None = None,
        hide_url: bool = True,
    ) -> dict[str, str]:
        """Generate standardized HTTP headers for INCY client."""
        title_b64 = base64.b64encode(profile_title.strip().encode("utf-8")).decode("ascii")

        headers = {
            "Content-Type": "text/plain; charset=utf-8",
            "Cache-Control": "no-store, private, no-cache, must-revalidate",
            "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff",
            "Profile-Title": f"base64:{title_b64}",
            "Profile-Update-Interval": str(max(1, update_interval_hours)),
            "Subscription-Userinfo": (
                f"upload={max(0, upload_bytes)};"
                f"download={max(0, download_bytes)};"
                f"total={max(0, total_quota_bytes)};"
                f"expire={max(0, expire_ts)}"
            ),
        }

        if hide_url:
            headers["hide-url"] = "1"

        if support_url and support_url.strip():
            headers["support-url"] = support_url.strip()

        return headers

    DUMMY_SINKHOLE_CONFIG: str = """[Interface]
Address = 10.255.255.2/32
DNS = 127.0.0.1
MTU = 1280
PrivateKey = aAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=
Jc = 4
Jmin = 10
Jmax = 50
S1 = 79
S2 = 115
S3 = 5
S4 = 1
H1 = 169154911-1234371153
H2 = 2057051984-2121122945
H3 = 2132872968-2133668229
H4 = 2136455412-2141801388

[Peer]
PublicKey = aAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=
AllowedIPs = 0.0.0.0/0, ::/0
Endpoint = 127.0.0.1:1
PersistentKeepalive = 25
"""

    @classmethod
    def create_stub_server(cls, message: str, flag: str = "🛑") -> Tuple[str, str, str]:
        """Create a non-routable stub server item for informative paywalls/notices."""
        return (cls.DUMMY_SINKHOLE_CONFIG, message, flag)
