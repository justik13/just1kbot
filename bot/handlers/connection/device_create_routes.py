import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from database.models import User
from services.maintenance_service import MaintenanceService

from .common import _render_connections, _render_maintenance

router = Router()
logger = logging.getLogger(__name__)


@router.callback_query(F.data == "add_device")
async def start_add_device(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
):
    user_id = callback.from_user.id

    if not await MaintenanceService.can_user_perform_action(session, user_id):
        await callback.answer(show_alert=False)
        await _render_maintenance(
            callback.message, session, back_to="back_to_connections"
        )
        return

    await state.clear()
    await callback.answer(
        texts.ALERT_AMNEZIA_DECOMMISSIONED,
        show_alert=True,
    )
    if db_user:
        await _render_connections(callback.message, db_user, session)
