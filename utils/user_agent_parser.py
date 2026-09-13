"""Lightweight parser for client User-Agent headers to provide human-readable device labels."""

from __future__ import annotations


def parse_device_model_from_ua(
    user_agent: str | None,
    fallback_index: int = 1,
    fallback_template: str | None = None,
) -> str:
    """Extract a friendly device label from User-Agent string.

    Recognizes iOS (iPhone, iPad), Android (with common vendors), Windows, Mac, and Linux.
    Falls back to a formatted default string if no platform is detected.
    """
    default_label = (
        fallback_template.format(index=fallback_index)
        if fallback_template
        else f"Device #{fallback_index} (INCY)"
    )

    if not user_agent or not isinstance(user_agent, str):
        return default_label

    ua = user_agent.strip()
    if not ua:
        return default_label

    ua_lower = ua.lower()

    # Apple mobile
    if "iphone" in ua_lower:
        return "📱 iPhone"
    if "ipad" in ua_lower:
        return "📱 iPad"

    # Android with vendor detection
    if "android" in ua_lower:
        if "samsung" in ua_lower or "sm-" in ua_lower:
            return "🤖 Samsung (Android)"
        if "pixel" in ua_lower:
            return "🤖 Google Pixel"
        if "xiaomi" in ua_lower or "redmi" in ua_lower or "poco" in ua_lower:
            return "🤖 Xiaomi (Android)"
        if "huawei" in ua_lower or "honor" in ua_lower:
            return "🤖 Huawei (Android)"
        return "🤖 Android"

    # Desktop / Other OS
    if "windows" in ua_lower:
        return "💻 Windows PC"
    if "macintosh" in ua_lower or ("mac os" in ua_lower and "iphone" not in ua_lower and "ipad" not in ua_lower):
        return "💻 Mac"
    if "linux" in ua_lower:
        return "💻 Linux PC"

    return default_label
