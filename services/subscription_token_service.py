import logging
import secrets
from typing import Optional

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
    @staticmethod
    def generate_token() -> str:
        # secrets.token_urlsafe(32) produces a 43-character string, which fits in VARCHAR(64)
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
                async with session.begin_nested():
                    user.subscription_token = new_token
                    await session.flush()
                return new_token
            except IntegrityError:
                logger.warning(
                    "Unique constraint collision or race on subscription_token for user %s, checking DB...",
                    user.id,
                )
                # Savepoint rollback is automatically performed by begin_nested()
                # Re-query user from DB to obtain token written by concurrent worker
                stmt = select(User.subscription_token).where(User.id == user.id)
                res = await session.execute(stmt)
                existing = res.scalar_one_or_none()
                if existing:
                    user.subscription_token = existing
                    return existing

        stmt = select(User.subscription_token).where(User.id == user.id)
        res = await session.execute(stmt)
        existing = res.scalar_one_or_none()
        if existing:
            user.subscription_token = existing
            return existing

        raise RuntimeError(
            f"Failed to generate persistent subscription token for user {user.id}"
        )

    @classmethod
    async def rotate_token(
        cls, session: AsyncSession, user: User
    ) -> str:
        for _attempt in range(5):
            new_token = cls.generate_token()
            try:
                async with session.begin_nested():
                    user.subscription_token = new_token
                    await session.flush()
                return new_token
            except IntegrityError:
                logger.warning(
                    "Collision during token rotation for user %s, retrying...",
                    user.id,
                )
                continue

        raise RuntimeError(
            f"Failed to rotate subscription token for user {user.id}"
        )

    @staticmethod
    async def get_user_by_token(
        session: AsyncSession, token: str
    ) -> Optional[User]:
        if not token or len(token) > MAX_SUBSCRIPTION_TOKEN_LENGTH:
            return None
        return await get_user_by_subscription_token(session, token)
