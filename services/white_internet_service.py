"""Business logic orchestration for White Internet subscriptions (VLESS over XHTTP via CDN)."""

from __future__ import annotations

import base64
import json
import logging
import urllib.parse

from bot import texts
from config.constants import (
    CANONICAL_XHTTP_PROFILE,
    DEFAULT_WHITE_INTERNET_PATH,
    WHITE_INTERNET_ORIGIN_BADGE,
    WHITE_INTERNET_RELAY_BADGE,
    WHITE_INTERNET_TLS_FINGERPRINT,
)
from database.models import WhiteInternetSubscription

logger = logging.getLogger(__name__)


def _normalize_base_path(path: str | None) -> str:
    cleaned = (path or "").strip().rstrip("/")
    if cleaned.endswith("/default"):
        cleaned = cleaned[:-8].rstrip("/")
    if not cleaned:
        cleaned = DEFAULT_WHITE_INTERNET_PATH.rstrip("/")
    if not cleaned.startswith("/"):
        cleaned = "/" + cleaned
    return cleaned


class WhiteInternetService:
    """Service managing White Internet VLESS over XHTTP link generation."""

    @staticmethod
    def _ensure_country_flag(name: str, code: str = "") -> str:
        clean_name = (name or "").strip()
        if not clean_name:
            return clean_name
        known_flags = (
            "🇷🇺", "🇩🇪", "🇸🇪", "🇪🇪", "🇫🇮", "🇳🇱", "🇵🇱",
            "🇫🇷", "🇬🇧", "🇺🇸", "🇹🇷", "🇰🇿", "🇦🇹", "🇨🇭",
        )
        if any(f in clean_name for f in known_flags):
            return clean_name

        c = (code or "").strip().lower()
        flag_map = {
            "ru": "🇷🇺", "de": "🇩🇪", "se": "🇸🇪", "ee": "🇪🇪",
            "fi": "🇫🇮", "nl": "🇳🇱", "pl": "🇵🇱", "fr": "🇫🇷",
            "gb": "🇬🇧", "us": "🇺🇸", "tr": "🇹🇷", "kz": "🇰🇿",
        }
        flag = flag_map.get(c, "🌐")
        return f"{flag} {clean_name}"

    @staticmethod
    def _format_vless_tag(tag_name: str, badge: str | None) -> str:
        clean_tag = tag_name.strip()
        quoted_tag = urllib.parse.quote(clean_tag)
        if badge and isinstance(badge, str) and badge.strip() and badge.strip().lower() != "none":
            clean_badge = badge.strip()[:30]
            b64_badge = base64.b64encode(clean_badge.encode("utf-8")).decode("ascii")
            return f"{quoted_tag}?serverDescription={b64_badge}"
        return quoted_tag

    @staticmethod
    def generate_vless_links(
        subscription: WhiteInternetSubscription,
        cdn_domain: str,
        port: int = 443,
        path: str = DEFAULT_WHITE_INTERNET_PATH,
        relays: list[dict] | None = None,
        include_origin: bool = True,
        origin_tag: str | None = None,
        origin_badge: str | None = None,
        default_relay_badge: str | None = None,
        relay_names: dict[str, str] | None = None,
        relay_badges: dict[str, str] | None = None,
    ) -> list[str]:
        extra_dict = {
            "mode": CANONICAL_XHTTP_PROFILE["mode"],
            "uplinkHTTPMethod": CANONICAL_XHTTP_PROFILE["uplinkHTTPMethod"],
            "uplinkDataPlacement": CANONICAL_XHTTP_PROFILE["uplinkDataPlacement"],
            "uplinkDataKey": CANONICAL_XHTTP_PROFILE["uplinkDataKey"],
            "scMaxEachPostBytes": CANONICAL_XHTTP_PROFILE["scMaxEachPostBytes"],
            "scMaxConcurrentPosts": CANONICAL_XHTTP_PROFILE["scMaxConcurrentPosts"],
            "scMinPostsIntervalMs": CANONICAL_XHTTP_PROFILE["scMinPostsIntervalMs"],
            "xPaddingObfsMode": CANONICAL_XHTTP_PROFILE["xPaddingObfsMode"],
            "xPaddingKey": CANONICAL_XHTTP_PROFILE["xPaddingKey"],
            "xPaddingHeader": CANONICAL_XHTTP_PROFILE["xPaddingHeader"],
            "xPaddingMethod": CANONICAL_XHTTP_PROFILE["xPaddingMethod"],
            "xPaddingPlacement": CANONICAL_XHTTP_PROFILE["xPaddingPlacement"],
            "xPaddingBytes": CANONICAL_XHTTP_PROFILE.get("xPaddingBytes", "100-1000"),
        }
        extra_param = urllib.parse.quote(json.dumps(extra_dict, separators=(",", ":")))
        fp = CANONICAL_XHTTP_PROFILE.get("fp", WHITE_INTERNET_TLS_FINGERPRINT)
        base = _normalize_base_path(path)

        links: list[str] = []

        if include_origin:
            default_origin_name = texts.WL_ORIGIN_VLESS_TAG
            origin_tag_str = (
                origin_tag.strip()
                if origin_tag and isinstance(origin_tag, str) and origin_tag.strip()
                else default_origin_name
            )
            origin_tag_str = WhiteInternetService._ensure_country_flag(origin_tag_str, "ru")
            effective_origin_badge = origin_badge or WHITE_INTERNET_ORIGIN_BADGE
            origin_tag_formatted = WhiteInternetService._format_vless_tag(origin_tag_str, effective_origin_badge)
            standalone_path = f"{base}/default"
            origin_link = (
                f"vless://{subscription.uuid}@{cdn_domain}:{port}"
                f"?encryption=none&security=tls&sni={cdn_domain}&alpn=h2&fp={fp}"
                f"&type=xhttp&path={urllib.parse.quote(standalone_path, safe='')}"
                f"&mode=packet-up&mux=false&extra={extra_param}#{origin_tag_formatted}"
            )
            links.append(origin_link)

        if relays and isinstance(relays, list):
            for r in relays:
                if not isinstance(r, dict):
                    continue
                relay_code = r.get("code") or r.get("name") or "default"
                r_path = r.get("path") or f"{base}/{relay_code}"
                custom_name = relay_names.get(relay_code) if (relay_names and isinstance(relay_names, dict)) else None
                r_name = (
                    custom_name.strip()
                    if custom_name and isinstance(custom_name, str) and custom_name.strip()
                    else (r.get("name") or relay_code)
                )
                r_name = WhiteInternetService._ensure_country_flag(r_name, relay_code)
                r_badge = (
                    relay_badges.get(relay_code)
                    if (relay_badges and isinstance(relay_badges, dict))
                    else default_relay_badge or WHITE_INTERNET_RELAY_BADGE
                )
                r_tag = WhiteInternetService._format_vless_tag(r_name, r_badge)
                link = (
                    f"vless://{subscription.uuid}@{cdn_domain}:{port}"
                    f"?encryption=none&security=tls&sni={cdn_domain}&alpn=h2&fp={fp}"
                    f"&type=xhttp&path={urllib.parse.quote(r_path, safe='')}"
                    f"&mode=packet-up&mux=false&extra={extra_param}#{r_tag}"
                )
                links.append(link)

        return links
