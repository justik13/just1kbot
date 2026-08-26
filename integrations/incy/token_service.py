import logging
import secrets

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import User
from database.repositories.users_repo import (
    get_user_by_subscription_token,
)

logger = logging.getLogger(__name__)

MAX_SUBSCRIPTION_TOKEN_LENGTH = 64


class SubscriptionTokenService:
    @classmethod
    def is_enabled(cls) -> bool:
        """Check if subscription feed / INCY feature is enabled and domain is available."""
        try:
            from config.settings import get_settings
            settings = get_settings()
            if not getattr(settings, "INCY_SUBSCRIPTION_ENABLED", True):
                return False
            domain = getattr(settings, "DOMAIN", "")
            return bool(domain and str(domain).strip())
        except Exception:
            return False

    @staticmethod
    def generate_token() -> str:
        # secrets.token_urlsafe(32) produces a 43-character string, safely fitting in VARCHAR(64)
        return secrets.token_urlsafe(32)

    @classmethod
    async def get_or_create_token(
        cls, session: AsyncSession, user: User
    ) -> str:
        if not user or not user.id:
            raise ValueError("Valid persisted user required for token operations")

        # Row lock with populate_existing ensures that concurrent commits refresh the identity map
        stmt = (
            select(User)
            .where(User.id == user.id, User.is_deleted.is_(False))
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        res = await session.execute(stmt)
        locked_user = res.scalar_one_or_none()
        if not locked_user:
            raise RuntimeError(f"User {user.id} not found or deleted")

        if locked_user.subscription_token:
            user.subscription_token = locked_user.subscription_token
            return locked_user.subscription_token

        for _attempt in range(5):
            new_token = cls.generate_token()
            try:
                async with session.begin_nested():
                    locked_user.subscription_token = new_token
                    await session.flush()
                user.subscription_token = new_token
                return new_token
            except IntegrityError:
                logger.warning(
                    "Unique token collision during subscription_token generation for user %s, retrying...",
                    user.id,
                )
                continue

        # If retries exhausted, re-check DB state under lock
        if locked_user.subscription_token:
            user.subscription_token = locked_user.subscription_token
            return locked_user.subscription_token

        raise RuntimeError(
            f"Failed to generate persistent subscription token for user {user.id}"
        )

    @classmethod
    async def rotate_token(
        cls, session: AsyncSession, user: User
    ) -> str:
        if not user or not user.id:
            raise ValueError("Valid persisted user required for token operations")

        stmt = (
            select(User)
            .where(User.id == user.id, User.is_deleted.is_(False))
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        res = await session.execute(stmt)
        locked_user = res.scalar_one_or_none()
        if not locked_user:
            raise RuntimeError(f"User {user.id} not found or deleted")

        for _attempt in range(5):
            new_token = cls.generate_token()
            try:
                async with session.begin_nested():
                    locked_user.subscription_token = new_token
                    await session.flush()
                user.subscription_token = new_token
                return new_token
            except IntegrityError:
                logger.warning(
                    "Unique token collision during token rotation for user %s, retrying...",
                    user.id,
                )
                continue

        raise RuntimeError(
            f"Failed to rotate subscription token for user {user.id}"
        )

    @staticmethod
    async def get_user_by_token(
        session: AsyncSession, token: str
    ) -> User | None:
        if not token or len(token) > MAX_SUBSCRIPTION_TOKEN_LENGTH:
            return None
        return await get_user_by_subscription_token(session, token)
