"""Backward-compatibility shim for Subscription Feed Service."""

from database.repositories.profiles_repo import get_user_profiles
from integrations.incy.feed_service import (
    SUPPORTED_SUBSCRIPTION_PROTOCOLS,
    SubscriptionFeedService,
)
from services.subscription import SubscriptionService
from utils.datetime_helpers import now_utc
from utils.vpn_parser import build_conf_file

__all__ = [
    "SUPPORTED_SUBSCRIPTION_PROTOCOLS",
    "SubscriptionFeedService",
    "SubscriptionService",
    "build_conf_file",
    "get_user_profiles",
    "now_utc",
]
