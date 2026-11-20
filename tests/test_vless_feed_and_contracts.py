"""Unit tests for subscription URL generation, header sanitization, and network contracts."""

import base64
import ipaddress
import urllib.parse

from bot.handlers.vless_web import (
    _make_inline_stub,
    _sanitize_ascii_header,
    _sanitize_header_url,
)
from services.xray_node_client import TrafficSnapshot
from utils.security import is_ip_allowed
from utils.text_limits import split_text_by_lines


def test_sanitize_header_url():
    # Only valid http/https URLs allowed, rejects dangerous schemes or non-ASCII
    assert _sanitize_header_url("https://example.com/sub") == "https://example.com/sub"
    assert _sanitize_header_url("http://example.com/sub") == "http://example.com/sub"
    assert _sanitize_header_url("javascript:alert(1)") == ""
    assert _sanitize_header_url("data:text/html;base64,abc") == ""
    assert _sanitize_header_url("https://example.com/тест") == ""
    assert _sanitize_header_url("https://example.com/\r\nHeader-Injection: 1") == ""


def test_sanitize_ascii_header():
    assert _sanitize_ascii_header("tlshello") == "tlshello"
    assert _sanitize_ascii_header("hello\r\nworld") == "hello  world"
    assert _sanitize_ascii_header(None, fallback="default") == "default"


def test_make_inline_stub_structure():
    stub = _make_inline_stub("Ваша подписка истекла", "Подробнее в боте")
    assert stub.startswith("vless://00000000-0000-0000-0000-000000000000@127.0.0.1:443?")
    # Check that serverDescription appears before the # fragment
    assert "&serverDescription=" in stub
    q_part, fragment = stub.split("#", 1)
    assert urllib.parse.unquote(fragment) == "Ваша подписка истекла"

    # Decode serverDescription from query params
    params = urllib.parse.parse_qs(q_part.split("?", 1)[1])
    assert "serverDescription" in params
    desc_decoded = base64.b64decode(params["serverDescription"][0]).decode("utf-8")
    assert desc_decoded == "Подробнее в боте"


def test_traffic_snapshot_structure():
    users_by_svc = {
        "vless": {"u1": {"uplink": 100, "downlink": 200}},
        "white_internet": {"u2": {"uplink": 300, "downlink": 400}},
    }
    snap = TrafficSnapshot("epoch1", "boot1", 1000, {"u1": {"uplink": 100}}, 5000, 6000, users_by_svc)
    assert snap[0] == "epoch1"
    assert snap[1] == "boot1"
    assert snap[2] == 1000
    assert snap.host_tx_bytes == 5000
    assert snap.host_rx_bytes == 6000
    assert snap.users_by_service["vless"]["u1"]["uplink"] == 100
    assert snap.users_by_service["white_internet"]["u2"]["downlink"] == 400


def test_split_text_by_lines_html_integrity():
    text = "Line 1\nLine 2\nLine 3"
    parts = split_text_by_lines(text, limit=15)
    assert len(parts) >= 2
    for p in parts:
        assert len(p) <= 15

    # Blockquote tag integrity check
    bq_text = "<blockquote>\nLong content line that must be split across multiple chunks\n</blockquote>"
    parts_bq = split_text_by_lines(bq_text, limit=40)
    for p in parts_bq:
        assert len(p) <= 40
        # Check no dangling unclosed open blockquote tag at end
        if "<blockquote" in p:
            assert "</blockquote>" in p


def test_security_is_ip_allowed():
    # CGNAT / Shared address space (100.64.0.0/10) is not global
    cgnat_ip = ipaddress.ip_address("100.64.0.1")
    assert not is_ip_allowed(cgnat_ip)

    # Private RFC 1918
    priv_ip = ipaddress.ip_address("192.168.1.1")
    assert not is_ip_allowed(priv_ip)
    assert is_ip_allowed(priv_ip, allow_local=True)

    # Public IP
    pub_ip = ipaddress.ip_address("1.1.1.1")
    assert is_ip_allowed(pub_ip)


def test_generate_vless_links_standard_tcp_vision():
    from database.models import Server, VlessSubscription
    from config.enums import ServerHealthState, ServerLifecycleStatus
    from services.vless_subscription_service import VlessSubscriptionService

    sub = VlessSubscription(uuid="11111111-2222-3333-4444-555555555555")
    ru_hub = Server(
        id=23,
        name="Россия Hub",
        is_active=True,
        health_state=ServerHealthState.ONLINE,
        lifecycle_status=ServerLifecycleStatus.ACTIVE,
        role="ru_hub",
        capabilities=["ru_hub", "vless"],
        extra_data={"domain": "ruhub.just1k.pro", "vless_port": 443},
    )
    ee_exit = Server(
        id=26,
        name="Эстония Exit",
        country_flag="🇪🇪",
        is_active=True,
        health_state=ServerHealthState.ONLINE,
        lifecycle_status=ServerLifecycleStatus.ACTIVE,
        role="exit_node",
        capabilities=["exit_node"],
        extra_data={"location_code": "ee", "vless_name": "Эстония Exit"},
    )

    links = VlessSubscriptionService.generate_vless_links(sub, [ru_hub], [ee_exit])
    assert len(links) == 1
    link = links[0]
    assert "type=tcp" in link
    assert "flow=xtls-rprx-vision" in link
    assert "security=tls" in link
    assert "alpn=http/1.1" in link
    assert "sni=ruhub.just1k.pro" in link
    assert "#%F0%9F%87%AA%F0%9F%87%AA%20%D0%AD%D1%81%D1%82%D0%BE%D0%BD%D0%B8%D1%8F" in link

