import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from database.models import User

from .common import _render_connections

router = Router()
logger = logging.getLogger(__name__)


@router.callback_query(F.data.startswith("migrate_device:"))
async def start_migrate_device(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
):
    await state.clear()
    await callback.answer(texts.ALERT_AMNEZIA_DECOMMISSIONED, show_alert=True)
    if db_user:
        await _render_connections(callback.message, db_user, session)


@router.callback_query(F.data.startswith("select_migrate_server:"))
async def select_migrate_target_server(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
):
    await state.clear()
    await callback.answer(texts.ALERT_AMNEZIA_DECOMMISSIONED, show_alert=True)
    if db_user:
        await _render_connections(callback.message, db_user, session)


@router.callback_query(F.data.startswith("confirm_migrate_device:"))
async def confirm_migrate_device(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
):
    await state.clear()
    await callback.answer(texts.ALERT_AMNEZIA_DECOMMISSIONED, show_alert=True)
    if db_user:
        await _render_connections(callback.message, db_user, session)
