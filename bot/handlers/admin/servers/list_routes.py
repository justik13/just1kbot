from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from utils.admin import is_admin

from .common import _show_servers_list

router = Router()


@router.callback_query(F.data.in_(["admin_servers", "admin_servers:std", "admin_servers:wi"]))
async def show_servers_list(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return

    await callback.answer(show_alert=False)
    await state.clear()

    filter_type = callback.data.split(":", 1)[1] if ":" in callback.data else None
    await _show_servers_list(callback, session, page=1, filter_type=filter_type)


@router.callback_query(F.data.startswith("admin_servers_page"))
async def servers_pagination(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
):
    if not is_admin(callback.from_user.id):
        await callback.answer(
            texts.ERROR_ACCESS_DENIED,
            show_alert=True,
        )
        return

    parts = callback.data.split(":")
    filter_type = None
    page = 1
    if len(parts) == 2:
        try:
            page = int(parts[1])
        except ValueError:
            page = 1
    elif len(parts) >= 3:
        filter_type = parts[1]
        try:
            page = int(parts[2])
        except ValueError:
            page = 1

    if filter_type not in ("std", "wi"):
        filter_type = None

    if page < 1:
        await callback.answer(
            texts.ERROR_INVALID_REQUEST,
            show_alert=True,
        )
        return

    await callback.answer(show_alert=False)
    await state.clear()

    await _show_servers_list(callback, session, page=page, filter_type=filter_type)