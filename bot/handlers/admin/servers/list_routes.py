from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from utils.admin import is_admin
from utils.callbacks import parse_callback_id

from .common import _show_servers_list

router = Router()


@router.callback_query(F.data == "admin_servers")
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

    await _show_servers_list(callback, session, page=1)


@router.callback_query(F.data.startswith("admin_servers_page:"))
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

    page = parse_callback_id(callback.data, 1)

    if page is None or page < 1:
        await callback.answer(
            "Некорректный запрос",
            show_alert=True,
        )
        return

    await callback.answer(show_alert=False)
    await state.clear()

    await _show_servers_list(callback, session, page=page)