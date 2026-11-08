"""Neutral, dependency-free system and protocol constants for Just1k 2.0.

This module sits at Level 0 in the application layered architecture and has zero
internal dependencies, allowing safe downward imports by utils, database,
integrations, services, and bot layers without architectural cycles.
"""

import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from config.enums import (
    AccountLedgerEntryType,
    AdminAuditAction,
    ApiOperationStatus,
    ApiOperationType,
    OrderStatus,
    OrderServiceType,
    PaymentCheckoutStatus,
    PaymentFulfillmentStatus,
    PaymentProviderStatus,
    PaymentReconciliationStatus,
    ServerHealthState,
    ServerLifecycleStatus,
    ServiceType,
    VPNProvisioningStatus,
    WebhookInboxStatus,
    WhiteInternetGrantType,
    WhiteInternetProvisioningStatus,
    WhiteInternetStatus,
)

# Protocols & Transports
PROTOCOL_VLESS = "vless"
VLESS_PROTOCOL = "vless"
TRANSPORT_TLS = "tls"
TRANSPORT_XHTTP = "xhttp"
XRAY_PROTOCOL = "xray"
XRAY_ORIGIN_CAPABILITY = "xray_origin"

# Pricing & Device Limits (Strictly from just1kbotv2.md)
# Standard: 99 RUB / month, 2 devices, unlimited traffic (VLESS TLS selfsteal)
STANDARD_BASE_PRICE_RUB = Decimal(os.getenv("STANDARD_BASE_PRICE_RUB", "99.00"))
STANDARD_BASE_DURATION_DAYS = int(os.getenv("STANDARD_BASE_DURATION_DAYS", "30"))
STANDARD_DEFAULT_DEVICE_LIMIT = int(os.getenv("STANDARD_DEFAULT_DEVICE_LIMIT", "2"))

# White Internet: 250 RUB / month, 1 device, 50 GB quota (VLESS XHTTP via CDN)
WHITE_INTERNET_SERVICE_TYPE = "white_internet"
WHITE_INTERNET_BASE_PRICE_RUB = Decimal(os.getenv("WHITE_INTERNET_BASE_PRICE_RUB", "250.00"))
WHITE_INTERNET_BASE_DURATION_DAYS = int(os.getenv("WHITE_INTERNET_BASE_DURATION_DAYS", "30"))
WHITE_INTERNET_BASE_TRAFFIC_BYTES = int(
    os.getenv("WHITE_INTERNET_BASE_TRAFFIC_BYTES", str(50 * 1024 * 1024 * 1024))
)  # 50 GiB
WHITE_INTERNET_DEFAULT_DEVICE_LIMIT = int(os.getenv("WHITE_INTERNET_DEFAULT_DEVICE_LIMIT", "1"))
WHITE_INTERNET_EXTRA_DEVICE_PRICE_RUB = Decimal(
    os.getenv("WHITE_INTERNET_EXTRA_DEVICE_PRICE_RUB", "200.00")
)
WHITE_INTERNET_EXTRA_DEVICE_TRAFFIC_BYTES = int(
    os.getenv("WHITE_INTERNET_EXTRA_DEVICE_TRAFFIC_BYTES", str(50 * 1024 * 1024 * 1024))
)  # +50 GiB
WHITE_INTERNET_MAX_DEVICE_LIMIT = 5

# Quota Topup Packs: 10 GB - 40 RUB
WHITE_INTERNET_TOPUP_PACKS: dict[int, Decimal] = {
    10: Decimal(os.getenv("WHITE_INTERNET_TOPUP_10GB_PRICE_RUB", "40.00")),
    25: Decimal(os.getenv("WHITE_INTERNET_TOPUP_25GB_PRICE_RUB", "100.00")),
    50: Decimal(os.getenv("WHITE_INTERNET_TOPUP_50GB_PRICE_RUB", "200.00")),
}

# Grace Period & Retention (7 days per just1kbotv2.md)
DEVICE_GRACE_PERIOD_DAYS = 7
GRACE_PERIOD_HOURS = 24 * DEVICE_GRACE_PERIOD_DAYS
VPN_ACCESS_GRACE_HOURS = 4
PERMANENT_SUBSCRIPTION_DAYS = 36500
PERMANENT_END_DATE = datetime(2100, 1, 1, tzinfo=timezone.utc)

# Rate Limits
RATE_LIMIT_REQUESTS_PER_MINUTE = 60.0
RATE_LIMIT_BURST = 15

# Subscriptions & Transport URLs
DEFAULT_WHITE_INTERNET_SUB_PATH_PREFIX = "/sub"
WHITE_INTERNET_SUB_PATH_PREFIX = "/sub"
DEFAULT_WHITE_INTERNET_PATH = os.getenv("WHITE_INTERNET_XHTTP_PATH", "/assets/v1")
DEFAULT_WHITE_INTERNET_PADDING_KEY = os.getenv("WHITE_INTERNET_PADDING_KEY", "dc")
WHITE_INTERNET_TLS_FINGERPRINT = os.getenv("WHITE_INTERNET_TLS_FINGERPRINT", "firefox")
DEFAULT_VLESS_TLS_FINGERPRINT = "firefox"
VLESS_TLS_FINGERPRINT = DEFAULT_VLESS_TLS_FINGERPRINT
VALID_VLESS_TLS_FINGERPRINTS = frozenset({
    "chrome",
    "firefox",
    "safari",
    "ios",
    "android",
    "edge",
    "random",
})

# YooKassa Official Webhook IP Ranges
YOOKASSA_IP_RANGES = (
    "185.71.76.0/27",
    "185.71.77.0/27",
    "77.75.153.0/25",
    "77.75.154.128/25",
    "77.75.156.11/32",
    "77.75.156.35/32",
    "2a02:5180::/32",
)

# Operational & Worker timings
STALE_PAYMENT_THRESHOLD = 300
WORKER_ERROR_SLEEP_INTERVAL = 60
NOTIFICATION_INTERVAL = 1800
TRAFFIC_SYNC_INTERVAL = 900
SELF_HEALING_MAX_PER_CYCLE = 50
API_CONCURRENCY_LIMIT = 20
API_RETRY_COUNT = 2
API_TIMEOUT = 15

# UI & Message limits
TELEGRAM_MESSAGE_LIMIT = 4096
HUB_CACHE_MAX_SIZE = 10000
HUB_CACHE_TTL = 43200
USER_CONTEXT_CACHE_MAX_SIZE = 2000
USER_CONTEXT_CACHE_TTL = 15.0

# Referral System Constants (From just1kbotv2.md)
# Ladder with qualification threshold of 67 RUB spend
REFERRAL_ACTIVE_MIN_TOPUP_RUB: Decimal = Decimal("67.00")
REFERRAL_TIERS: tuple[tuple[int, Decimal, str], ...] = (
    (0, Decimal("0.15"), "Standard"),
    (3, Decimal("0.20"), "Silver"),
    (7, Decimal("0.25"), "Gold"),
    (15, Decimal("0.30"), "Platinum"),
)
REFERRAL_DEFAULT_RATE: Decimal = Decimal("0.15")
REFERRAL_WELCOME_DISCOUNT_PERCENT: Decimal = Decimal("0.10")

# Badges and Titles (Neutral for user, technical internally)
DEFAULT_FALLBACK_BOT_USERNAME = "just1kbot"
DEFAULT_VLESS_PROFILE_TITLE = "Just1k Standard"
DEFAULT_WHITE_INTERNET_PROFILE_TITLE = "Just1k Safe"
WHITE_INTERNET_ORIGIN_BADGE = "RU Hub"
WHITE_INTERNET_RELAY_BADGE = "CDN"
WHITE_INTERNET_CHANNEL_URL = "https://t.me/just1k_news"
WHITE_INTERNET_SUPPORT_URL = "https://t.me/just1k_support"
DEFAULT_WHITE_INTERNET_CHANNEL_URL = WHITE_INTERNET_CHANNEL_URL
DEFAULT_WHITE_INTERNET_SUPPORT_URL = WHITE_INTERNET_SUPPORT_URL
DEFAULT_XRAY_ORIGIN_MAX_CLIENTS = 1000
DEVICE_DAILY_LIMIT = 25
MAX_RAW_CONFIG_BYTES = 65536
WHITE_INTERNET_HWID_TTL_HOURS = 48
WHITE_INTERNET_MAX_EXPIRY_DAYS = 60
WHITE_INTERNET_DEVICE_RESET_COOLDOWN_SECONDS = 900
WHITE_INTERNET_MAX_QUOTA_BYTES = 200 * 1024 * 1024 * 1024
WHITE_INTERNET_PROFILE_TITLE = DEFAULT_WHITE_INTERNET_PROFILE_TITLE

# Telegram message effect IDs (Bot API 7.4+)
EFFECT_LIKE = "5107584321108051014"
EFFECT_FIRE = "5104841245755180586"
EFFECT_CONFETTI = "5046509860389126442"

CANONICAL_XHTTP_PROFILE: dict[str, Any] = {
    "mode": "packet-up",
    "uplinkHTTPMethod": "GET",
    "uplinkDataPlacement": "header",
    "uplinkDataKey": "data",
    "scMaxEachPostBytes": 4096,
    "scMaxConcurrentPosts": 1,
    "scMinPostsIntervalMs": 30,
    "xPaddingPlacement": "queryInHeader",
    "xPaddingKey": DEFAULT_WHITE_INTERNET_PADDING_KEY,
    "xPaddingHeader": "X-Cache",
    "xPaddingMethod": "tokenish",
    "xPaddingObfsMode": True,
    "security": "tls",
    "alpn": ["h2", "http/1.1"],
    "fp": WHITE_INTERNET_TLS_FINGERPRINT,
}

__all__ = [
    "API_CONCURRENCY_LIMIT",
    "API_RETRY_COUNT",
    "API_TIMEOUT",
    "AccountLedgerEntryType",
    "AdminAuditAction",
    "ApiOperationStatus",
    "ApiOperationType",
    "CANONICAL_XHTTP_PROFILE",
    "DEFAULT_FALLBACK_BOT_USERNAME",
    "DEFAULT_VLESS_PROFILE_TITLE",
    "DEFAULT_VLESS_TLS_FINGERPRINT",
    "DEFAULT_WHITE_INTERNET_CHANNEL_URL",
    "DEFAULT_WHITE_INTERNET_PATH",
    "DEFAULT_WHITE_INTERNET_PROFILE_TITLE",
    "DEFAULT_WHITE_INTERNET_SUB_PATH_PREFIX",
    "DEFAULT_WHITE_INTERNET_SUPPORT_URL",
    "DEFAULT_XRAY_ORIGIN_MAX_CLIENTS",
    "DEVICE_DAILY_LIMIT",
    "DEVICE_GRACE_PERIOD_DAYS",
    "EFFECT_CONFETTI",
    "EFFECT_FIRE",
    "EFFECT_LIKE",
    "GRACE_PERIOD_HOURS",
    "HUB_CACHE_MAX_SIZE",
    "HUB_CACHE_TTL",
    "MAX_RAW_CONFIG_BYTES",
    "NOTIFICATION_INTERVAL",
    "OrderStatus",
    "OrderServiceType",
    "PERMANENT_END_DATE",
    "PERMANENT_SUBSCRIPTION_DAYS",
    "PaymentCheckoutStatus",
    "PaymentFulfillmentStatus",
    "PaymentProviderStatus",
    "PaymentReconciliationStatus",
    "PROTOCOL_VLESS",
    "RATE_LIMIT_BURST",
    "RATE_LIMIT_REQUESTS_PER_MINUTE",
    "REFERRAL_ACTIVE_MIN_TOPUP_RUB",
    "REFERRAL_DEFAULT_RATE",
    "REFERRAL_TIERS",
    "REFERRAL_WELCOME_DISCOUNT_PERCENT",
    "SELF_HEALING_MAX_PER_CYCLE",
    "STANDARD_BASE_DURATION_DAYS",
    "STANDARD_BASE_PRICE_RUB",
    "STANDARD_DEFAULT_DEVICE_LIMIT",
    "STALE_PAYMENT_THRESHOLD",
    "ServerHealthState",
    "ServerLifecycleStatus",
    "ServiceType",
    "TELEGRAM_MESSAGE_LIMIT",
    "TRAFFIC_SYNC_INTERVAL",
    "TRANSPORT_TLS",
    "TRANSPORT_XHTTP",
    "USER_CONTEXT_CACHE_MAX_SIZE",
    "USER_CONTEXT_CACHE_TTL",
    "VALID_VLESS_TLS_FINGERPRINTS",
    "VLESS_PROTOCOL",
    "VLESS_TLS_FINGERPRINT",
    "VPN_ACCESS_GRACE_HOURS",
    "VPNProvisioningStatus",
    "WHITE_INTERNET_BASE_DURATION_DAYS",
    "WHITE_INTERNET_BASE_PRICE_RUB",
    "WHITE_INTERNET_BASE_TRAFFIC_BYTES",
    "WHITE_INTERNET_CHANNEL_URL",
    "WHITE_INTERNET_DEFAULT_DEVICE_LIMIT",
    "WHITE_INTERNET_DEVICE_RESET_COOLDOWN_SECONDS",
    "WHITE_INTERNET_EXTRA_DEVICE_PRICE_RUB",
    "WHITE_INTERNET_EXTRA_DEVICE_TRAFFIC_BYTES",
    "WHITE_INTERNET_HWID_TTL_HOURS",
    "WHITE_INTERNET_MAX_DEVICE_LIMIT",
    "WHITE_INTERNET_MAX_EXPIRY_DAYS",
    "WHITE_INTERNET_MAX_QUOTA_BYTES",
    "WHITE_INTERNET_ORIGIN_BADGE",
    "WHITE_INTERNET_PROFILE_TITLE",
    "WHITE_INTERNET_RELAY_BADGE",
    "WHITE_INTERNET_SERVICE_TYPE",
    "WHITE_INTERNET_SUB_PATH_PREFIX",
    "WHITE_INTERNET_SUPPORT_URL",
    "WHITE_INTERNET_TOPUP_PACKS",
    "WORKER_ERROR_SLEEP_INTERVAL",
    "WebhookInboxStatus",
    "WhiteInternetGrantType",
    "WhiteInternetProvisioningStatus",
    "WhiteInternetStatus",
    "XRAY_ORIGIN_CAPABILITY",
    "XRAY_PROTOCOL",
    "YOOKASSA_IP_RANGES",
]
