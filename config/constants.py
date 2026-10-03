"""Neutral, dependency-free system and protocol constants.

This module sits at Level 0 in the application layered architecture and has zero
internal dependencies, allowing safe downward imports by utils, database,
integrations, services, and bot layers without architectural cycles.
"""

import os
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from config.enums import (
    AccountLedgerEntryType,
    AdminAuditAction,
    ApiOperationStatus,
    ApiOperationType,
    OrderServiceType,
    OrderStatus,
    PaymentCheckoutStatus,
    PaymentFulfillmentStatus,
    PaymentProviderStatus,
    PaymentReconciliationStatus,
    ServerHealthState,
    ServerLifecycleStatus,
    ServiceType,
    TariffQuoteOperation,
    TariffQuoteStatus,
    VPNProvisioningStatus,
    WebhookInboxStatus,
    WhiteInternetGrantType,
    WhiteInternetProvisioningStatus,
    WhiteInternetStatus,
)


# Protocol
AMNEZIA_PROTOCOL = "amneziawg2"
AMNEZIA_PROTOCOLS: tuple[str, ...] = (
    AMNEZIA_PROTOCOL,
    "amneziawg3",
    "amneziawg3.1",
)
AMNEZIA_DOCKER_CONTAINER = "amnezia-awg2"
XRAY_PROTOCOL = "xray"
XRAY_ORIGIN_CAPABILITY = "xray_origin"

# Default AmneziaWG Client Network Settings
DEFAULT_AWG_DNS1 = "8.8.8.8"
DEFAULT_AWG_DNS2 = "8.8.4.4"
DEFAULT_AWG_MTU = "1280"

# VPN Configuration transport limits
MAX_RAW_CONFIG_BYTES = 65536  # 64 KiB max raw config payload

# Default HTTP rate limiter settings
RATE_LIMIT_REQUESTS_PER_MINUTE = 30.0
RATE_LIMIT_BURST = 10

# Subscriptions & Lifetimes
PERMANENT_SUBSCRIPTION_DAYS = 36500
PERMANENT_END_DATE = datetime(2100, 1, 1, tzinfo=timezone.utc)
GRACE_PERIOD_HOURS = 24
VPN_ACCESS_GRACE_HOURS = 4

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

# Device limits
DEVICE_DAILY_LIMIT = 25

# Operational & Worker timings
STALE_PAYMENT_THRESHOLD = 300
WORKER_ERROR_SLEEP_INTERVAL = 60
NOTIFICATION_INTERVAL = 1800
TRAFFIC_SYNC_INTERVAL = 900
SELF_HEALING_MAX_PER_CYCLE = 50

# Local payment-expiry window: the cleanup worker marks a payment canceled
# only after a provider GET still reports pending (provider-verified LOCAL
# API Client timings and concurrency
API_CONCURRENCY_LIMIT = 20
API_RETRY_COUNT = 2
API_TIMEOUT = 15

# UI & Message limits
TELEGRAM_MESSAGE_LIMIT = 4096

# In-memory Caches
HUB_CACHE_MAX_SIZE = 10000
HUB_CACHE_TTL = 43200
USER_CONTEXT_CACHE_MAX_SIZE = 2000
USER_CONTEXT_CACHE_TTL = 15.0


# White Internet Service Constants
WHITE_INTERNET_SERVICE_TYPE = "white_internet"
WHITE_INTERNET_BASE_PRICE_RUB = Decimal(os.getenv("WHITE_INTERNET_BASE_PRICE_RUB", "250.00"))
WHITE_INTERNET_BASE_DURATION_DAYS = int(os.getenv("WHITE_INTERNET_BASE_DURATION_DAYS", "30"))
WHITE_INTERNET_BASE_TRAFFIC_BYTES = int(
    os.getenv("WHITE_INTERNET_BASE_TRAFFIC_BYTES", str(53_687_091_200))
)  # 50 GiB
WHITE_INTERNET_TRIAL_MODE_ONLY = os.getenv("WHITE_INTERNET_TRIAL_MODE_ONLY", "false").lower() in (
    "true",
    "1",
    "yes",
)
WHITE_INTERNET_TRIAL_DURATION_DAYS = int(os.getenv("WHITE_INTERNET_TRIAL_DURATION_DAYS", "3"))
WHITE_INTERNET_TRIAL_TRAFFIC_BYTES = int(
    os.getenv("WHITE_INTERNET_TRIAL_TRAFFIC_BYTES", str(5 * 1024 * 1024 * 1024))
)  # 5 GiB
WHITE_INTERNET_EXTRA_DEVICE_PRICE_RUB = Decimal(
    os.getenv("WHITE_INTERNET_EXTRA_DEVICE_PRICE_RUB", "200.00")
)
WHITE_INTERNET_EXTRA_DEVICE_TRAFFIC_BYTES = int(
    os.getenv("WHITE_INTERNET_EXTRA_DEVICE_TRAFFIC_BYTES", str(53_687_091_200))
)  # 50 GiB
WHITE_INTERNET_MAX_DEVICE_LIMIT = min(
    3, max(1, int(os.getenv("WHITE_INTERNET_MAX_DEVICE_LIMIT", "3")))
)  # Enforced by PostgreSQL CheckConstraint: 1 <= device_limit <= 3
WHITE_INTERNET_HWID_TTL_HOURS = int(os.getenv("WHITE_INTERNET_HWID_TTL_HOURS", "48"))
WHITE_INTERNET_MAX_EXPIRY_DAYS = int(os.getenv("WHITE_INTERNET_MAX_EXPIRY_DAYS", "60"))
WHITE_INTERNET_DEVICE_RESET_COOLDOWN_SECONDS = int(
    os.getenv("WHITE_INTERNET_DEVICE_RESET_COOLDOWN_SECONDS", "900")
)  # 15 minutes
WHITE_INTERNET_MAX_QUOTA_BYTES = int(
    os.getenv("WHITE_INTERNET_MAX_QUOTA_BYTES", str(161_061_273_600))
)  # 150 GiB
WHITE_INTERNET_TOPUP_PACKS: dict[int, Decimal] = {
    10: Decimal(os.getenv("WHITE_INTERNET_TOPUP_10GB_PRICE_RUB", "40.00")),
    25: Decimal(os.getenv("WHITE_INTERNET_TOPUP_25GB_PRICE_RUB", "100.00")),
    50: Decimal(os.getenv("WHITE_INTERNET_TOPUP_50GB_PRICE_RUB", "200.00")),
}
SUPPORTED_MIN_XRAY_VERSION = "26.5.9"
DEFAULT_PINNED_XRAY_VERSION = "26.7.28"
DEFAULT_XRAY_ORIGIN_MAX_CLIENTS = 1000
DEFAULT_WHITE_INTERNET_PATH = os.getenv("WHITE_INTERNET_XHTTP_PATH", "/assets/v1")
DEFAULT_WHITE_INTERNET_PADDING_KEY = os.getenv("WHITE_INTERNET_PADDING_KEY", "dc")
WHITE_INTERNET_TLS_FINGERPRINT = os.getenv("WHITE_INTERNET_TLS_FINGERPRINT", "firefox")
DEFAULT_WHITE_INTERNET_SUB_PATH_PREFIX = "/sub/wl"
WHITE_INTERNET_SUB_PATH_PREFIX = (
    os.getenv("WHITE_INTERNET_SUB_PATH_PREFIX", DEFAULT_WHITE_INTERNET_SUB_PATH_PREFIX)
    .strip()
    .rstrip("/")
)
if not WHITE_INTERNET_SUB_PATH_PREFIX.startswith("/"):
    WHITE_INTERNET_SUB_PATH_PREFIX = f"/{WHITE_INTERNET_SUB_PATH_PREFIX}"


WHITE_INTERNET_DEFAULT_DEVICE_LIMIT: int = 1

DEFAULT_WHITE_INTERNET_PROFILE_TITLE = "✦ Just1k"
WHITE_INTERNET_PROFILE_TITLE = os.getenv(
    "WHITE_INTERNET_PROFILE_TITLE", DEFAULT_WHITE_INTERNET_PROFILE_TITLE
).strip()

DEFAULT_WHITE_INTERNET_PROFILE_DESCRIPTION = ""
WHITE_INTERNET_PROFILE_DESCRIPTION = os.getenv(
    "WHITE_INTERNET_PROFILE_DESCRIPTION", DEFAULT_WHITE_INTERNET_PROFILE_DESCRIPTION
).strip()

DEFAULT_WHITE_INTERNET_CHANNEL_URL = os.getenv("TELEGRAM_CHANNEL_URL", "").strip()
WHITE_INTERNET_CHANNEL_URL = os.getenv(
    "WHITE_INTERNET_CHANNEL_URL", DEFAULT_WHITE_INTERNET_CHANNEL_URL
).strip()

DEFAULT_WHITE_INTERNET_SUPPORT_URL = (
    f"https://t.me/{os.getenv('SUPPORT_BOT_USERNAME', '').lstrip('@')}"
    if os.getenv("SUPPORT_BOT_USERNAME")
    else ""
)
WHITE_INTERNET_SUPPORT_URL = os.getenv(
    "WHITE_INTERNET_SUPPORT_URL", DEFAULT_WHITE_INTERNET_SUPPORT_URL
).strip()

WHITE_INTERNET_ORIGIN_BADGE = os.getenv("WHITE_INTERNET_ORIGIN_BADGE", "").strip()
WHITE_INTERNET_RELAY_BADGE = os.getenv("WHITE_INTERNET_RELAY_BADGE", "").strip()


def _validate_xhttp_padding_bytes(val: str | None) -> str:
    default_val = "100-1000"
    if not val or not isinstance(val, str):
        return default_val
    clean = val.strip()
    if re.match(r"^\d+-\d+$|^\d+$", clean):
        return clean
    return default_val


# Referral System Constants
REFERRAL_TIERS: tuple[tuple[int, Decimal, str], ...] = (
    (0, Decimal("0.15"), "Уровень 1"),
    (3, Decimal("0.20"), "Уровень 2"),
    (7, Decimal("0.25"), "Уровень 3"),
    (15, Decimal("0.30"), "Уровень 4"),
)
REFERRAL_DEFAULT_RATE: Decimal = Decimal("0.15")
REFERRAL_WELCOME_DISCOUNT_PERCENT: Decimal = Decimal("0.25")
# Minimum qualifying top-up for a referral to become "active".
# Dust top-ups below this threshold neither activate the referral
# (leaderboard/tier counting) nor consume the inviter's tier step.
# 68 RUB = discounted first Base-30 tariff (90 - 25%): the smallest *real*
# purchase. A referred test-week buyer pays 27 (35 - 25%) and intentionally
# does NOT activate; White Internet buyers (188+ discounted) always qualify.
# Independent business constant, NOT auto-derived from tariffs: if tariff
# prices or the welcome rate change, adjust it manually in one place.
REFERRAL_ACTIVE_MIN_TOPUP_RUB: Decimal = Decimal("68")


CANONICAL_XHTTP_PROFILE: dict[str, Any] = {
    "mode": "packet-up",
    # Uplink without request bodies: Yandex Cloud CDN edge answers 413 to ANY
    # request carrying a body (observed 28-29.09.2026, all methods). Header
    # placement keeps uploads bodiless (data travels in data-{i} headers).
    "uplinkHTTPMethod": "GET",
    "uplinkDataPlacement": "header",
    "uplinkDataKey": "data",
    # Keep a single upload post within the server-side scMaxEachPostBytes
    # limit (Xray itself answers 413 when a post exceeds it).
    "scMaxEachPostBytes": 4096,
    "scMaxConcurrentPosts": 1,
    "scMinPostsIntervalMs": 30,
    "xPaddingPlacement": "queryInHeader",
    "xPaddingKey": DEFAULT_WHITE_INTERNET_PADDING_KEY,
    "xPaddingHeader": "X-Cache",
    "xPaddingMethod": "tokenish",
    "xPaddingObfsMode": True,
    "xPaddingBytes": _validate_xhttp_padding_bytes(os.getenv("WHITE_INTERNET_PADDING_BYTES")),
    "security": "tls",
    "alpn": ["h2", "http/1.1"],
    "fp": WHITE_INTERNET_TLS_FINGERPRINT,
}


__all__ = [
    "AMNEZIA_DOCKER_CONTAINER",
    "AMNEZIA_PROTOCOL",
    "AMNEZIA_PROTOCOLS",
    "API_CONCURRENCY_LIMIT",
    "API_RETRY_COUNT",
    "API_TIMEOUT",
    "AccountLedgerEntryType",
    "AdminAuditAction",
    "ApiOperationStatus",
    "CANONICAL_XHTTP_PROFILE",
    "ApiOperationType",
    "DEFAULT_AWG_DNS1",
    "DEFAULT_AWG_DNS2",
    "DEFAULT_AWG_MTU",
    "DEFAULT_PINNED_XRAY_VERSION",
    "DEFAULT_WHITE_INTERNET_CHANNEL_URL",
    "DEFAULT_WHITE_INTERNET_PROFILE_DESCRIPTION",
    "DEFAULT_WHITE_INTERNET_PROFILE_TITLE",
    "DEFAULT_WHITE_INTERNET_SUPPORT_URL",
    "DEFAULT_XRAY_ORIGIN_MAX_CLIENTS",
    "DEVICE_DAILY_LIMIT",
    "OrderServiceType",
    "OrderStatus",
    "GRACE_PERIOD_HOURS",
    "HUB_CACHE_MAX_SIZE",
    "HUB_CACHE_TTL",
    "MAX_RAW_CONFIG_BYTES",
    "NOTIFICATION_INTERVAL",
    "PERMANENT_END_DATE",
    "PERMANENT_SUBSCRIPTION_DAYS",
    "PaymentCheckoutStatus",
    "PaymentFulfillmentStatus",
    "PaymentProviderStatus",
    "PaymentReconciliationStatus",
    "RATE_LIMIT_BURST",
    "RATE_LIMIT_REQUESTS_PER_MINUTE",
    "REFERRAL_DEFAULT_RATE",
    "REFERRAL_TIERS",
    "REFERRAL_ACTIVE_MIN_TOPUP_RUB",
    "REFERRAL_WELCOME_DISCOUNT_PERCENT",
    "SELF_HEALING_MAX_PER_CYCLE",
    "ServerHealthState",
    "ServerLifecycleStatus",
    "ServiceType",
    "STALE_PAYMENT_THRESHOLD",
    "TELEGRAM_MESSAGE_LIMIT",
    "TRAFFIC_SYNC_INTERVAL",
    "TariffQuoteOperation",
    "TariffQuoteStatus",
    "USER_CONTEXT_CACHE_MAX_SIZE",
    "USER_CONTEXT_CACHE_TTL",
    "DEFAULT_WHITE_INTERNET_SUB_PATH_PREFIX",
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
    "WHITE_INTERNET_MAX_DEVICE_LIMIT",
    "WHITE_INTERNET_MAX_EXPIRY_DAYS",
    "WHITE_INTERNET_MAX_QUOTA_BYTES",
    "WHITE_INTERNET_ORIGIN_BADGE",
    "WHITE_INTERNET_PROFILE_DESCRIPTION",
    "WHITE_INTERNET_PROFILE_TITLE",
    "WHITE_INTERNET_RELAY_BADGE",
    "WHITE_INTERNET_SERVICE_TYPE",
    "WHITE_INTERNET_SUB_PATH_PREFIX",
    "WHITE_INTERNET_SUPPORT_URL",
    "WHITE_INTERNET_TOPUP_PACKS",
    "WHITE_INTERNET_TRIAL_DURATION_DAYS",
    "WHITE_INTERNET_TRIAL_MODE_ONLY",
    "WHITE_INTERNET_TRIAL_TRAFFIC_BYTES",
    "WebhookInboxStatus",
    "WhiteInternetGrantType",
    "WhiteInternetProvisioningStatus",
    "WhiteInternetStatus",
    "WORKER_ERROR_SLEEP_INTERVAL",
    "XRAY_ORIGIN_CAPABILITY",
    "XRAY_PROTOCOL",
    "YOOKASSA_IP_RANGES",
]
