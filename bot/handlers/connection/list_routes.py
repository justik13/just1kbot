from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from database.models import User

from .common import _render_connections

router = Router()


@router.callback_query(F.data.in_({"menu_connections", "back_to_connections"}))
async def hub_menu_connections(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
):
    await callback.answer(show_alert=False)
    await state.clear()

    if not db_user:
        try:
            await callback.answer(
                texts.ERROR_USER_NOT_FOUND,
                show_alert=True,
            )
        except Exception:
            pass
        return

    await _render_connections(
        callback.message,
        db_user,
        session,
    )


@router.callback_query(F.data.in_({"amnezia_devices", "back_to_amnezia_devices"}))
async def hub_amnezia_devices(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
):
    await state.clear()
    await callback.answer(
        texts.ALERT_AMNEZIA_DECOMMISSIONED,
        show_alert=True,
    )
    if db_user:
        await _render_connections(callback.message, db_user, session)
