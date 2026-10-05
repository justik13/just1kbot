"""Unit tests for configurable INCY subscription appearance (titles, badges, URLs, headers)."""

from __future__ import annotations

import base64
import os
import unittest
import urllib.parse
from contextlib import asynccontextmanager
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase

from bot.handlers.white_internet_web import setup_white_internet_web_routes
from config.constants import (
    XRAY_PROTOCOL,
)
from config.enums import ServerHealthState, WhiteInternetStatus
from database.models import Server, WhiteInternetSubscription
from services.white_internet_service import WhiteInternetService
from utils.datetime_helpers import now_utc


class TestWhiteInternetIncyConfig(unittest.TestCase):
    """Tests for VLESS tag formatting and ?serverDescription parameter generation."""

    def test_format_vless_tag_without_badge(self):
        tag = WhiteInternetService._format_vless_tag("🇷🇺 Россия", None)
        self.assertEqual(tag, urllib.parse.quote("🇷🇺 Россия"))
        self.assertNotIn("serverDescription", tag)

    def test_format_vless_tag_with_empty_or_none_string(self):
        tag_empty = WhiteInternetService._format_vless_tag("🇩🇪 Германия", "")
        self.assertEqual(tag_empty, urllib.parse.quote("🇩🇪 Германия"))
        self.assertNotIn("serverDescription", tag_empty)

        tag_none = WhiteInternetService._format_vless_tag("🇩🇪 Германия", "none")
        self.assertEqual(tag_none, urllib.parse.quote("🇩🇪 Германия"))
        self.assertNotIn("serverDescription", tag_none)

    def test_format_vless_tag_with_badge_base64_roundtrip(self):
        badge_text = "⚡ Прямой шлюз"
        tag = WhiteInternetService._format_vless_tag("🇷🇺 Россия", badge_text)
        self.assertIn("?serverDescription=", tag)

        parts = tag.split("?serverDescription=")
        self.assertEqual(parts[0], urllib.parse.quote("🇷🇺 Россия"))

        b64_val = parts[1]
        decoded = base64.b64decode(b64_val).decode("utf-8")
        self.assertEqual(decoded, badge_text)

    def test_format_vless_tag_truncates_long_badges(self):
        long_badge = "A" * 50
        tag = WhiteInternetService._format_vless_tag("Server", long_badge)
        b64_val = tag.split("?serverDescription=")[1]
        decoded = base64.b64decode(b64_val).decode("utf-8")
        self.assertEqual(len(decoded), 30)

    def test_generate_vless_links_applies_origin_and_relay_badges(self):
        sub = MagicMock(spec=WhiteInternetSubscription)
        sub.uuid = "a2b9d4e1-73c5-4812-b964-f3e7b85a1902"
        relays = [
            {"code": "de", "name": "🇩🇪 Германия"},
            {"code": "nl", "name": "🇳🇱 Нидерланды"},
        ]

        links = WhiteInternetService.generate_vless_links(
            sub,
            cdn_domain="cdn.just1k.online",
            relays=relays,
            origin_tag="🇷🇺 РФ Премиум",
            origin_badge="⚡ Шлюз РФ",
            relay_names={"de": "🇩🇪 Франкфурт"},
            relay_badges={"de": "⚡ YouTube БЕЗ рекламы"},
        )

        self.assertEqual(len(links), 3)

        # 1. Origin link: custom origin_tag and origin_badge
        origin_link = links[0]
        self.assertIn(urllib.parse.quote("🇷🇺 РФ Премиум"), origin_link)
        self.assertIn("serverDescription=", origin_link)
        self.assertIn(base64.b64encode("⚡ Шлюз РФ".encode()).decode(), origin_link)

        # 2. Relay 'de': custom relay name and custom badge
        de_link = links[1]
        self.assertIn(urllib.parse.quote("🇩🇪 Франкфурт"), de_link)
        self.assertIn("serverDescription=", de_link)
        self.assertIn(base64.b64encode("⚡ YouTube БЕЗ рекламы".encode()).decode(), de_link)

        # 3. Relay 'nl': no custom badge -> completely clean without serverDescription=
        nl_link = links[2]
        self.assertIn(urllib.parse.quote("🇳🇱 Нидерланды"), nl_link)
        self.assertNotIn("serverDescription=", nl_link)

    def test_generate_vless_links_clean_when_no_badges_configured(self):
        sub = MagicMock(spec=WhiteInternetSubscription)
        sub.uuid = "a2b9d4e1-73c5-4812-b964-f3e7b85a1902"
        relays = [{"code": "nl", "name": "🇳🇱 Нидерланды"}]
        links = WhiteInternetService.generate_vless_links(
            sub,
            cdn_domain="cdn.just1k.online",
            relays=relays,
        )
        self.assertEqual(len(links), 2)
        self.assertNotIn("serverDescription=", links[0])
        self.assertNotIn("serverDescription=", links[1])

    def test_origin_hidden_strict_when_no_relays(self):
        sub = MagicMock(spec=WhiteInternetSubscription)
        sub.uuid = "a2b9d4e1-73c5-4812-b964-f3e7b85a1902"
        # When origin is hidden (include_origin=False) and relays is empty,
        # return empty list rather than forcing origin to be included.
        links = WhiteInternetService.generate_vless_links(
            sub,
            cdn_domain="cdn.just1k.online",
            relays=[],
            include_origin=False,
        )
        self.assertEqual(links, [])

    def test_badge_none_suppresses_fallback_completely(self):
        sub = MagicMock(spec=WhiteInternetSubscription)
        sub.uuid = "a2b9d4e1-73c5-4812-b964-f3e7b85a1902"
        relays = [{"code": "de", "name": "🇩🇪 Германия"}]
        with patch("services.white_internet_service.WHITE_INTERNET_ORIGIN_BADGE", "GlobalOriginBadge"):
            with patch("services.white_internet_service.WHITE_INTERNET_RELAY_BADGE", "GlobalRelayBadge"):
                links = WhiteInternetService.generate_vless_links(
                    sub,
                    cdn_domain="cdn.just1k.online",
                    relays=relays,
                    origin_badge="none",
                    relay_badges={"de": "none"},
                )
                self.assertEqual(len(links), 2)
                # Both origin and relay should have no serverDescription=
                self.assertNotIn("serverDescription=", links[0])
                self.assertNotIn("serverDescription=", links[1])

    def test_generate_vless_links_and_full_config_mux_disabled_and_upload_tuning(self):
        import json
        sub = MagicMock(spec=WhiteInternetSubscription)
        sub.uuid = "a2b9d4e1-73c5-4812-b964-f3e7b85a1902"
        relays = [{"code": "de", "name": "🇩🇪 Германия"}]
        links = WhiteInternetService.generate_vless_links(
            sub,
            cdn_domain="cdn.just1k.online",
            relays=relays,
        )
        self.assertEqual(len(links), 2)
        for link in links:
            # 1. mux=false must be explicitly present in link parameters
            self.assertIn("&mux=false", link)
            # 2. extra param must contain fast upload params without custom xmux override
            parsed = urllib.parse.urlparse(link)
            query = urllib.parse.parse_qs(parsed.query)
            self.assertIn("extra", query)
            extra_data = json.loads(query["extra"][0])
            self.assertNotIn("xmux", extra_data)
            self.assertEqual(extra_data["scMaxEachPostBytes"], 16384)
            self.assertEqual(extra_data["scMinPostsIntervalMs"], 10)

        # 3. generate_full_xray_config must contain mux: {enabled: False} and fast upload params without custom xmux
        cfg = WhiteInternetService.generate_full_xray_config(
            sub,
            cdn_domain="cdn.just1k.online",
        )
        outbound = cfg["outbounds"][0]
        self.assertEqual(outbound["mux"], {"enabled": False})
        xhttp_settings = outbound["streamSettings"]["xhttpSettings"]
        self.assertNotIn("xmux", xhttp_settings)
        self.assertEqual(xhttp_settings["scMaxEachPostBytes"], 16384)
        self.assertEqual(xhttp_settings["scMinPostsIntervalMs"], 10)


class TestWhiteInternetIncyWebHeaders(AioHTTPTestCase):
    """Test HTTP subscription feed headers with configurable INCY options."""

    async def get_application(self):
        app = web.Application()
        setup_white_internet_web_routes(app)
        return app

    async def test_feed_headers_clean_by_default_without_description(self):
        now = now_utc()
        sub = WhiteInternetSubscription(
            id=1,
            user_id=10,
            origin_node_id=1,
            token="valid-token-clean-1234567890abcdef",
            uuid="a2b9d4e1-73c5-4812-b964-f3e7b85a1902",
            status=WhiteInternetStatus.ACTIVE,
            started_at=now,
            expires_at=now + timedelta(days=30),
            traffic_limit_bytes=53687091200,
            traffic_used_bytes=1000,
            traffic_uplink_bytes=500,
            traffic_downlink_bytes=500,
            desired_version=1,
            actual_version=1,
            last_reconciled_node_epoch="epoch-xyz",
            device_limit=1,
            active_hwids={},
        )
        server = Server(
            id=1,
            name="Origin-Node",
            protocol=XRAY_PROTOCOL,
            api_url="https://cdn.just1k.online:8444",
            xray_instance_epoch="epoch-xyz",
            capabilities=["xray_origin"],
            is_active=True,
            health_state=ServerHealthState.ONLINE,
            extra_data={},
        )
        mock_session = AsyncMock()
        mock_session.scalar.return_value = server
        mock_session.execute.return_value = MagicMock(scalar_one_or_none=lambda: server)
        mock_session.get.return_value = sub

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with patch.dict(os.environ, {"WHITE_INTERNET_CDN_DOMAIN": "cdn.just1k.online"}):
            with patch("bot.handlers.white_internet_web.session_scope", fake_session_scope):
                with patch(
                    "database.repositories.white_internet_repo.get_subscription_by_token",
                    return_value=sub,
                ):
                    resp = await self.client.get(
                        f"/sub/wl/{sub.token}",
                        headers={"X-Hwid": "test-device"},
                    )
                    self.assertEqual(resp.status, 200)
                    self.assertIsNone(resp.headers.get("Profile-Description"))
                    self.assertIn("t.me", resp.headers.get("Support-Url", ""))
                    self.assertIn("t.me", resp.headers.get("Profile-Web-Page-Url", ""))

    async def test_feed_headers_customized_via_extra_data(self):
        now = now_utc()
        sub = WhiteInternetSubscription(
            id=2,
            user_id=10,
            origin_node_id=2,
            token="valid-token-custom-1234567890abcdef",
            uuid="a2b9d4e1-73c5-4812-b964-f3e7b85a1902",
            status=WhiteInternetStatus.ACTIVE,
            started_at=now,
            expires_at=now + timedelta(days=30),
            traffic_limit_bytes=53687091200,
            traffic_used_bytes=1000,
            traffic_uplink_bytes=500,
            traffic_downlink_bytes=500,
            desired_version=1,
            actual_version=1,
            last_reconciled_node_epoch="epoch-xyz",
            device_limit=1,
            active_hwids={},
        )
        server = Server(
            id=2,
            name="Origin-Custom",
            protocol=XRAY_PROTOCOL,
            api_url="https://cdn.just1k.online:8444",
            xray_instance_epoch="epoch-xyz",
            capabilities=["xray_origin"],
            is_active=True,
            health_state=ServerHealthState.ONLINE,
            extra_data={
                "profile_title": "★ VIP Just1k",
                "profile_description": "Премиальный доступ",
                "announce": "Внимание: технические работы",
                "announce_url": "https://t.me/just1k_channel/123",
                "channel_url": "https://t.me/just1k_channel",
                "support_url": "https://t.me/just1k_support",
                "origin_badge": "⚡ Максимальная скорость",
            },
        )
        mock_session = AsyncMock()
        mock_session.scalar.return_value = server
        mock_session.execute.return_value = MagicMock(scalar_one_or_none=lambda: server)
        mock_session.get.return_value = sub

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with patch.dict(os.environ, {"WHITE_INTERNET_CDN_DOMAIN": "cdn.just1k.online"}):
            with patch("bot.handlers.white_internet_web.session_scope", fake_session_scope):
                with patch(
                    "database.repositories.white_internet_repo.get_subscription_by_token",
                    return_value=sub,
                ):
                    resp = await self.client.get(
                        f"/sub/wl/{sub.token}",
                        headers={"X-Hwid": "test-device"},
                    )
                    self.assertEqual(resp.status, 200)

                    # Title
                    title_b64 = resp.headers.get("Profile-Title", "").split("base64:")[1]
                    self.assertEqual(base64.b64decode(title_b64).decode("utf-8"), "★ VIP Just1k")

                    # Description
                    desc_b64 = resp.headers.get("Profile-Description", "").split("base64:")[1]
                    self.assertEqual(base64.b64decode(desc_b64).decode("utf-8"), "Премиальный доступ")

                    # Announce banner
                    announce_b64 = resp.headers.get("Announce", "").split("base64:")[1]
                    self.assertEqual(base64.b64decode(announce_b64).decode("utf-8"), "Внимание: технические работы")
                    self.assertEqual(resp.headers.get("Announce-Url"), "https://t.me/just1k_channel/123")

                    # Zero-config system headers (canonical casing)
                    self.assertEqual(resp.headers.get("Hide-Url"), "1")
                    self.assertEqual(resp.headers.get("Hide-Check"), "1")
                    self.assertEqual(resp.headers.get("Sort-Order"), "none")
                    self.assertEqual(resp.headers.get("No-Limit-Enabled"), "1")
                    self.assertEqual(resp.headers.get("CDN-Cache-Control"), "no-store")
                    self.assertEqual(resp.headers.get("Profile-Update-Interval"), "6")

                    # Action buttons
                    self.assertEqual(resp.headers.get("Profile-Web-Page-Url"), "https://t.me/just1k_channel")
                    self.assertEqual(resp.headers.get("Support-Url"), "https://t.me/just1k_support")

                    # Body VLESS links badge
                    body_b64 = await resp.text()
                    decoded_body = base64.b64decode(body_b64).decode("utf-8")
                    self.assertIn("serverDescription=", decoded_body)
                    self.assertIn(base64.b64encode("⚡ Максимальная скорость".encode()).decode(), decoded_body)

    async def test_feed_headers_dynamic_device_templates(self):
        now = now_utc()
        sub = WhiteInternetSubscription(
            id=42,
            user_id=10,
            origin_node_id=2,
            token="valid-token-template-1234567890abcdef",
            uuid="a2b9d4e1-73c5-4812-b964-f3e7b85a1902",
            status=WhiteInternetStatus.ACTIVE,
            started_at=now,
            expires_at=now + timedelta(days=30),
            traffic_limit_bytes=53687091200,
            traffic_used_bytes=1000,
            traffic_uplink_bytes=500,
            traffic_downlink_bytes=500,
            desired_version=1,
            actual_version=1,
            last_reconciled_node_epoch="epoch-xyz",
            device_limit=5,
            active_hwids={"dev-existing": now.isoformat()},
        )
        server = Server(
            id=2,
            name="Origin-Custom",
            protocol=XRAY_PROTOCOL,
            api_url="https://cdn.just1k.online:8444",
            xray_instance_epoch="epoch-xyz",
            capabilities=["xray_origin"],
            is_active=True,
            health_state=ServerHealthState.ONLINE,
            extra_data={
                "profile_description": "Лимит: {devices}, активно: {active}",
                "announce": "Подключено: {active_devices} из {limit} устройств",
            },
        )
        mock_session = AsyncMock()
        mock_session.scalar.return_value = server
        mock_session.execute.return_value = MagicMock(scalar_one_or_none=lambda: server)
        mock_session.get.return_value = sub

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with patch.dict(os.environ, {"WHITE_INTERNET_CDN_DOMAIN": "cdn.just1k.online"}):
            with patch("bot.handlers.white_internet_web.session_scope", fake_session_scope):
                with patch(
                    "database.repositories.white_internet_repo.get_subscription_by_token",
                    return_value=sub,
                ):
                    with patch(
                        "database.repositories.white_internet_repo.register_hwid_atomic",
                        return_value=(True, 2, 5),
                    ):
                        resp = await self.client.get(
                            f"/sub/wl/{sub.token}",
                            headers={"X-Hwid": "dev-2"},
                        )
                        self.assertEqual(resp.status, 200)

                        desc_b64 = resp.headers.get("Profile-Description", "").split("base64:")[1]
                        self.assertEqual(base64.b64decode(desc_b64).decode("utf-8"), "Лимит: 5, активно: 2")

                        announce_b64 = resp.headers.get("Announce", "").split("base64:")[1]
                        self.assertEqual(
                            base64.b64decode(announce_b64).decode("utf-8"),
                            "Подключено: 2 из 5 устройств",
                        )

    async def test_web_feed_hidden_origin_and_relays(self):
        now = now_utc()
        sub = WhiteInternetSubscription(
            id=3,
            user_id=11,
            origin_node_id=3,
            token="valid-token-hidden-test-1234567890",
            uuid="b3c8d5e2-84d6-4923-a175-f4e8b96b2913",
            status=WhiteInternetStatus.ACTIVE,
            started_at=now,
            expires_at=now + timedelta(days=30),
            traffic_limit_bytes=53687091200,
            traffic_used_bytes=0,
            traffic_uplink_bytes=0,
            traffic_downlink_bytes=0,
            desired_version=1,
            actual_version=1,
            last_reconciled_node_epoch="epoch-xyz",
            device_limit=1,
            active_hwids={},
        )
        server = Server(
            id=3,
            name="Origin-RU",
            protocol=XRAY_PROTOCOL,
            api_url="https://cdn.just1k.online:8444",
            xray_instance_epoch="epoch-xyz",
            capabilities=["xray_origin"],
            is_active=True,
            health_state=ServerHealthState.ONLINE,
            extra_data={
                "origin_hidden": True,
                "relays": [
                    {"code": "de", "name": "Германия"},
                    {"code": "nl", "name": "Нидерланды"},
                ],
                "relay_hidden": ["de"],
            },
        )
        mock_session = AsyncMock()
        mock_session.scalar.return_value = server
        mock_session.execute.return_value = MagicMock(scalar_one_or_none=lambda: server)
        mock_session.get.return_value = sub

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with patch.dict(os.environ, {"WHITE_INTERNET_CDN_DOMAIN": "cdn.just1k.online"}):
            with patch("bot.handlers.white_internet_web.session_scope", fake_session_scope):
                with patch(
                    "database.repositories.white_internet_repo.get_subscription_by_token",
                    return_value=sub,
                ):
                    resp = await self.client.get(
                        f"/sub/wl/{sub.token}",
                        headers={"X-Hwid": "test-device"},
                    )
                    self.assertEqual(resp.status, 200)

                    body_b64 = await resp.text()
                    decoded_body = base64.b64decode(body_b64).decode("utf-8")
                    links = [line for line in decoded_body.splitlines() if line.strip()]

                    # Origin is hidden and 'de' is hidden -> only 'nl' relay should be returned
                    self.assertEqual(len(links), 1)
                    self.assertIn("nl", links[0])
                    self.assertNotIn("default", links[0])
                    self.assertNotIn("/de", links[0])

    async def test_profile_title_truncated_to_25_chars(self):
        now = now_utc()
        sub = WhiteInternetSubscription(
            id=4,
            user_id=40,
            origin_node_id=1,
            token="valid-token-title-len-check",
            uuid="a2b9d4e1-73c5-4812-b964-f3e7b85a1902",
            status=WhiteInternetStatus.ACTIVE,
            started_at=now,
            expires_at=now + timedelta(days=30),
            traffic_limit_bytes=53687091200,
            traffic_used_bytes=1000,
            traffic_uplink_bytes=500,
            traffic_downlink_bytes=500,
            desired_version=1,
            actual_version=1,
            last_reconciled_node_epoch="epoch-xyz",
            device_limit=1,
            active_hwids={},
        )
        long_title = "A" * 50
        server = Server(
            id=1,
            name="Origin-Node",
            protocol=XRAY_PROTOCOL,
            api_url="https://cdn.just1k.online:8444",
            xray_instance_epoch="epoch-xyz",
            capabilities=["xray_origin"],
            is_active=True,
            health_state=ServerHealthState.ONLINE,
            extra_data={"profile_title": long_title},
        )
        mock_session = AsyncMock()
        mock_session.scalar.return_value = server
        mock_session.execute.return_value = MagicMock(scalar_one_or_none=lambda: server)
        mock_session.get.return_value = sub

        @asynccontextmanager
        async def fake_session_scope():
            yield mock_session

        with patch.dict(os.environ, {"WHITE_INTERNET_CDN_DOMAIN": "cdn.just1k.online"}):
            with patch("bot.handlers.white_internet_web.session_scope", fake_session_scope):
                with patch(
                    "database.repositories.white_internet_repo.get_subscription_by_token",
                    return_value=sub,
                ):
                    resp = await self.client.get(
                        f"/sub/wl/{sub.token}",
                        headers={"X-Hwid": "test-device"},
                    )
                    self.assertEqual(resp.status, 200)
                    title_b64 = resp.headers.get("Profile-Title", "").split("base64:")[1]
                    decoded_title = base64.b64decode(title_b64).decode("utf-8")
                    self.assertEqual(len(decoded_title), 25)
                    self.assertEqual(decoded_title, "A" * 25)

    async def test_web_feed_error_headers_have_cdn_cache_control(self):
        # 1. Invalid short token (404)
        resp_404 = await self.client.get("/sub/wl/short")
        self.assertEqual(resp_404.status, 404)
        self.assertEqual(resp_404.headers.get("CDN-Cache-Control"), "no-store")
        self.assertEqual(resp_404.headers.get("Cache-Control"), "no-store")

        # 2. Rate limit IP exhaustion (429)
        with patch("bot.handlers.white_internet_web._ip_rate_limiter.check", return_value=(False, 30)):
            resp_429 = await self.client.get("/sub/wl/valid-length-token-12345")
            self.assertEqual(resp_429.status, 429)
            self.assertEqual(resp_429.headers.get("CDN-Cache-Control"), "no-store")
            self.assertEqual(resp_429.headers.get("Cache-Control"), "no-store")
            self.assertEqual(resp_429.headers.get("Retry-After"), "30")

    def test_ensure_country_flag_logic(self):
        # Auto prepend flag
        self.assertEqual(WhiteInternetService._ensure_country_flag("Швеция", "se"), "🇸🇪 Швеция")
        self.assertEqual(WhiteInternetService._ensure_country_flag("Германия", "de"), "🇩🇪 Германия")
        self.assertEqual(WhiteInternetService._ensure_country_flag("Эстония", "ee"), "🇪🇪 Эстония")
        self.assertEqual(WhiteInternetService._ensure_country_flag("Россия", "ru"), "🇷🇺 Россия")

        # Do not duplicate if already present
        self.assertEqual(WhiteInternetService._ensure_country_flag("🇸🇪 Швеция", "se"), "🇸🇪 Швеция")
        self.assertEqual(WhiteInternetService._ensure_country_flag("🇩🇪 Германия", "de"), "🇩🇪 Германия")
        self.assertEqual(WhiteInternetService._ensure_country_flag("🇷🇺 Россия", "ru"), "🇷🇺 Россия")

        # Keep non-country names as is
        self.assertEqual(
            WhiteInternetService._ensure_country_flag("⚪️ Белый Интернет (XHTTP)", ""),
            "⚪️ Белый Интернет (XHTTP)",
        )

    def test_generate_vless_links_origin_default_is_russia_without_parentheses(self):
        sub = MagicMock(spec=WhiteInternetSubscription)
        sub.uuid = "a2b9d4e1-73c5-4812-b964-f3e7b85a1902"
        relays = [
            {"code": "se", "name": "Швеция"},
            {"code": "de", "name": "Германия"},
        ]
        links = WhiteInternetService.generate_vless_links(
            sub,
            cdn_domain="cdn.example.com",
            relays=relays,
        )
        self.assertEqual(len(links), 3)

        # Origin link: must be "🇷🇺 Россия" without "(Прямой выход)"
        origin_link = links[0]
        self.assertIn(urllib.parse.quote("🇷🇺 Россия"), origin_link)
        self.assertNotIn("Прямой выход", origin_link)

        # Relay links: must have flags prepended automatically
        se_link = links[1]
        self.assertIn(urllib.parse.quote("🇸🇪 Швеция"), se_link)

        de_link = links[2]
        self.assertIn(urllib.parse.quote("🇩🇪 Германия"), de_link)
