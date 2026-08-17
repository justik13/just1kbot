import logging
import secrets
from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import User
from database.repositories.users_repo import (
    get_user_by_subscription_token,
)

logger = logging.getLogger(__name__)


class SubscriptionTokenService:
    @staticmethod
    def generate_token() -> str:
        return secrets.token_urlsafe(32)

    @classmethod
    async def get_or_create_token(
        cls, session: AsyncSession, user: User
    ) -> str:
        if user.subscription_token:
            return user.subscription_token

        for _attempt in range(5):
            new_token = cls.generate_token()
            try:
                user.subscription_token = new_token
                await session.flush()
                return new_token
            except IntegrityError:
                # Concurrent token creation or rare collision
                logger.warning(
                    "Collision or race during subscription_token generation for user %s, retrying...",
                    user.id,
                )
                await session.rollback()
                # Refresh user from DB to check if another worker generated it
                if user.subscription_token:
                    return user.subscription_token

        # Fallback if retry loop finishes
        return user.subscription_token or cls.generate_token()

    @classmethod
    async def rotate_token(
        cls, session: AsyncSession, user: User
    ) -> str:
        new_token = cls.generate_token()
        user.subscription_token = new_token
        await session.flush()
        return new_token

    @staticmethod
    async def get_user_by_token(
        session: AsyncSession, token: str
    ) -> Optional[User]:
        if not token or len(token) > 128:
            return None
        return await get_user_by_subscription_token(session, token)
