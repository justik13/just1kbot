"""Neutral, dependency-free system and protocol constants.

This module sits at Level 0 in the application layered architecture and has zero
internal dependencies, allowing safe downward imports by utils, database,
integrations, services, and bot layers without architectural cycles.
"""

from datetime import datetime, timezone

from config.enums import (
    AccountLedgerEntryType,
    AccountReservationStatus,
    AccountReservationType,
    ApiOperationStatus,
    ApiOperationType,
    EntitlementEntryType,
    PaidValueEntryType,
    PaymentCheckoutStatus,
    PaymentDisputeStatus,
    PaymentFulfillmentStatus,
    PaymentProviderStatus,
    PaymentReconciliationStatus,
    ProviderRefundOperationStatus,
    ServerHealthState,
    TariffQuoteOperation,
    TariffQuoteStatus,
    WebhookInboxStatus,
)

# Protocol
AMNEZIA_PROTOCOL = "amneziawg2"

# VPN Configuration transport limits
MAX_RAW_CONFIG_BYTES = 65536  # 64 KiB max raw config payload

# Default HTTP rate limiter settings
RATE_LIMIT_REQUESTS_PER_MINUTE = 30.0
RATE_LIMIT_BURST = 10

# Subscriptions & Lifetimes
PERMANENT_SUBSCRIPTION_DAYS = 36500
PERMANENT_END_DATE = datetime(2100, 1, 1, tzinfo=timezone.utc)
GRACE_PERIOD_HOURS = 24

# Device limits
DEVICE_DAILY_LIMIT = 25

# Operational & Worker timings
STALE_PAYMENT_THRESHOLD = 300
WORKER_ERROR_SLEEP_INTERVAL = 60
NOTIFICATION_INTERVAL = 1800
TRAFFIC_SYNC_INTERVAL = 900
SELF_HEALING_MAX_PER_CYCLE = 50

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


__all__ = [
    "AMNEZIA_PROTOCOL",
    "API_CONCURRENCY_LIMIT",
    "API_RETRY_COUNT",
    "API_TIMEOUT",
    "AccountLedgerEntryType",
    "AccountReservationStatus",
    "AccountReservationType",
    "ApiOperationStatus",
    "ApiOperationType",
    "DEVICE_DAILY_LIMIT",
    "EntitlementEntryType",
    "GRACE_PERIOD_HOURS",
    "HUB_CACHE_MAX_SIZE",
    "HUB_CACHE_TTL",
    "MAX_RAW_CONFIG_BYTES",
    "NOTIFICATION_INTERVAL",
    "PERMANENT_END_DATE",
    "PERMANENT_SUBSCRIPTION_DAYS",
    "PaidValueEntryType",
    "PaymentCheckoutStatus",
    "PaymentDisputeStatus",
    "PaymentFulfillmentStatus",
    "PaymentProviderStatus",
    "PaymentReconciliationStatus",
    "ProviderRefundOperationStatus",
    "RATE_LIMIT_BURST",
    "RATE_LIMIT_REQUESTS_PER_MINUTE",
    "SELF_HEALING_MAX_PER_CYCLE",
    "ServerHealthState",
    "STALE_PAYMENT_THRESHOLD",
    "TELEGRAM_MESSAGE_LIMIT",
    "TRAFFIC_SYNC_INTERVAL",
    "TariffQuoteOperation",
    "TariffQuoteStatus",
    "USER_CONTEXT_CACHE_MAX_SIZE",
    "USER_CONTEXT_CACHE_TTL",
    "WebhookInboxStatus",
    "WORKER_ERROR_SLEEP_INTERVAL",
]
