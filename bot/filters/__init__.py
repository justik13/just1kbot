"""Centralized admin authorization filter for aiogram routers.

Apply this filter at the router level so that every handler registered
under an admin router is automatically protected. Individual ``is_admin()``
guards inside each handler remain as defence-in-depth but are no longer the
*only* line of defence.
"""

from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message, TelegramObject

from utils.admin import is_admin


class AdminFilter(BaseFilter):
    """Fail-closed filter: rejects any update whose sender is not an admin."""

    async def __call__(self, event: TelegramObject) -> bool:
        user = None
        if isinstance(event, (Message, CallbackQuery)):
            user = event.from_user
        if user is None:
            return False
        return is_admin(user.id)
