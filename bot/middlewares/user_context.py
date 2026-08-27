from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import User
from database.repositories.users_repo import (
    create_user,
    get_user_by_telegram_id_any,
)
from services.user_cache import (
    clear_user_cache,
    get_cached_user_id,
    invalidate_user_cache,
    set_cached_user_id,
)

logger = logging.getLogger(__name__)

__all__ = [
    "UserContextMiddleware",
    "clear_user_cache",
    "invalidate_user_cache",
]



class UserContextMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Message | CallbackQuery,
        data: dict[str, Any],
    ) -> Any:
        telegram_id: int | None = None

        if isinstance(event, Message) and event.from_user or isinstance(event, CallbackQuery) and event.from_user:
            telegram_id = event.from_user.id

        if telegram_id is None:
            data["db_user"] = None
            return await handler(event, data)

        session: AsyncSession | None = data.get("session")
        if session is None:
            data["db_user"] = None
            return await handler(event, data)

        user: User | None = None
        is_cached, cached_user_id = get_cached_user_id(telegram_id)

        if is_cached:
            if cached_user_id is None:
                data["db_user"] = None
                return await handler(event, data)
            stmt = select(User).where(
                User.id == cached_user_id,
                User.is_deleted.is_(False),
            )
            result = await session.execute(stmt)
            user = result.scalar_one_or_none()
            if user is None:
                invalidate_user_cache(telegram_id)

        if user is None:
            stmt = select(User).where(
                User.telegram_id == telegram_id,
                User.is_deleted.is_(False),
            )
            result = await session.execute(stmt)
            user = result.scalar_one_or_none()

            if user is None:
                existing_any = await get_user_by_telegram_id_any(
                    session,
                    telegram_id,
                )
                if existing_any is not None and existing_any.is_deleted:
                    user = None
                    set_cached_user_id(telegram_id, None)
                elif existing_any is not None and not existing_any.is_deleted:
                    user = existing_any
                    set_cached_user_id(telegram_id, user.id)
                else:
                    try:
                        async with session.begin_nested():
                            user = await create_user(
                                session,
                                telegram_id=telegram_id,
                                username=event.from_user.username,
                                first_name=event.from_user.first_name,
                                referred_by=None,
                            )
                        set_cached_user_id(telegram_id, user.id)
                        logger.info(
                            "Auto-registered user %s on %s",
                            telegram_id,
                            type(event).__name__,
                        )
                    except IntegrityError:
                        existing_any = await get_user_by_telegram_id_any(
                            session,
                            telegram_id,
                        )
                        if existing_any is not None and not existing_any.is_deleted:
                            user = existing_any
                            set_cached_user_id(telegram_id, user.id)
                        else:
                            user = None
                            set_cached_user_id(telegram_id, None)
            else:
                set_cached_user_id(telegram_id, user.id)

        data["db_user"] = user
        return await handler(event, data)

