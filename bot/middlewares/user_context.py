from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message
from cachetools import TTLCache
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from bot.constants import USER_CONTEXT_CACHE_MAX_SIZE, USER_CONTEXT_CACHE_TTL
from database.models import User
from database.repositories.users_repo import (
    create_user,
    get_user_by_telegram_id_any,
)

logger = logging.getLogger(__name__)

_user_cache: TTLCache[int, int | None] = TTLCache(
    maxsize=USER_CONTEXT_CACHE_MAX_SIZE,
    ttl=USER_CONTEXT_CACHE_TTL,
)

_SENTINEL = object()


def invalidate_user_cache(telegram_id: int) -> None:
    _user_cache.pop(telegram_id, None)


def clear_user_cache() -> None:
    """Полная очистка кэша пользователей."""
    _user_cache.clear()


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
        cached_user_id = _user_cache.get(telegram_id, _SENTINEL)

        if cached_user_id is not _SENTINEL:
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
                _user_cache.pop(telegram_id, None)

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
                    _user_cache[telegram_id] = None
                elif existing_any is not None and not existing_any.is_deleted:
                    user = existing_any
                    _user_cache[telegram_id] = user.id
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
                        _user_cache[telegram_id] = user.id
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
                            _user_cache[telegram_id] = user.id
                        else:
                            user = None
                            _user_cache[telegram_id] = None
            else:
                _user_cache[telegram_id] = user.id

        data["db_user"] = user
        return await handler(event, data)

