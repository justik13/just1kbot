"""Shared helpers used by more than one handler domain.

Kept intentionally tiny: these functions were previously duplicated verbatim
(or with dangerously swapped signatures) across payment/common.py and
connection/common.py.
"""
from sqlalchemy.ext.asyncio import AsyncSession

from bot.keyboards import get_back_button
from services.maintenance_service import MaintenanceService
from services.subscription import SubscriptionService
from utils.telegram import render_hub


async def get_effective_device_limit(
    session: AsyncSession,
    user,
) -> int:
    return await SubscriptionService.get_effective_device_limit(session, user)


async def render_maintenance(
    target,
    session: AsyncSession,
    *,
    back_to: str = "back_to_main_menu",
) -> None:
    if target is None:
        return
    bot = getattr(target, "bot", None)
    chat = getattr(target, "chat", None)
    chat_id = chat.id if chat else None
    if (bot is None or chat_id is None):
        from aiogram.types import CallbackQuery

        if isinstance(target, CallbackQuery):
            bot = target.bot
            chat_id = target.message.chat.id if target.message else None
    if bot is None or chat_id is None:
        return
    message = await MaintenanceService.get_message(session)
    await render_hub(
        bot,
        chat_id,
        message,
        get_back_button(back_to),
    )
