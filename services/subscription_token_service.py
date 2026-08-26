"""Backward-compatibility shim for Subscription Token Service."""

from database.repositories.users_repo import get_user_by_subscription_token
from integrations.incy.token_service import (
    MAX_SUBSCRIPTION_TOKEN_LENGTH,
    SubscriptionTokenService,
)

__all__ = [
    "SubscriptionTokenService",
    "MAX_SUBSCRIPTION_TOKEN_LENGTH",
    "get_user_by_subscription_token",
]
