from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from database.models import User

from .common import _render_connections

router = Router()


@router.callback_query(F.data.startswith("rename_device:"))
async def rename_device_start(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
):
    await state.clear()
    await callback.answer(texts.ALERT_AMNEZIA_DECOMMISSIONED, show_alert=True)
    if db_user:
        await _render_connections(callback.message, db_user, session)
