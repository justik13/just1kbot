"""Legacy AWG callback router: safely intercepts decommissioned Amnezia callbacks."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from database.models import User
from .common import _render_connections

logger = logging.getLogger(__name__)
router = Router()

_LEGACY_EXACT_CALLBACKS = {
    "add_device",
    "amnezia_devices",
    "back_to_amnezia_devices",
}


@router.callback_query(
    F.data.in_(_LEGACY_EXACT_CALLBACKS)
    | F.data.startswith("migrate_device:")
    | F.data.startswith("select_migrate_server:")
    | F.data.startswith("confirm_migrate_device:")
    | F.data.startswith("rename_device:")
    | F.data.startswith("manage_device:")
    | F.data.startswith("device_help:")
    | F.data.startswith("alt_connection:")
    | F.data.startswith("download_conf:")
    | F.data.startswith("request_delete_device:")
    | F.data.startswith("cancel_delete_device:")
    | F.data.startswith("confirm_delete_device:")
    | F.data.startswith("delete_device:")
)
async def handle_decommissioned_amnezia_action(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
) -> None:
    await state.clear()
    await callback.answer(texts.ALERT_AMNEZIA_DECOMMISSIONED, show_alert=True)
    if db_user:
        await _render_connections(callback.message, db_user, session)
