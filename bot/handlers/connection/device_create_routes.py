import asyncio
import logging
import time

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from database.connection import get_session
from database.models import User, VPNProfile
from services.maintenance_service import MaintenanceService

from .common import _render_maintenance

router = Router()
logger = logging.getLogger(__name__)


async def _await_profile_ready(
    profile_id: int,
    timeout_seconds: float = 15.0,
    poll_interval: float = 0.1,
) -> VPNProfile | None:
    """Poll for profile to become active or fail within a monotonic UI wait window.

    Uses short-lived independent read sessions to prevent identity-map staleness
    and avoid holding DB connections from the pool during sleeps.
    """
    deadline = time.monotonic() + timeout_seconds
    while True:
        session = await get_session()
        try:
            profile = await session.get(VPNProfile, profile_id)
            from .device_view_routes import is_profile_ready_for_user
            if is_profile_ready_for_user(profile):
                return profile
            if profile and profile.provisioning_status in ("create_failed", "create_cleanup_pending"):
                return profile
        finally:
            await session.close()

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        await asyncio.sleep(min(poll_interval, remaining))


def _classify_server_error(error_msg: str) -> str:
    msg_lower = error_msg.lower()

    if "full" in msg_lower:
        return "full"
    if "disabled" in msg_lower:
        return "disabled"
    if "busy" in msg_lower:
        return "busy"
    if "verify" in msg_lower or "slots" in msg_lower:
        return "slots_unknown"
    if "api" in msg_lower or "create_user" in msg_lower:
        return "api_failed"
    if "db error" in msg_lower:
        return "db_error"

    return "unknown"


def _get_server_error_text(error_type: str) -> str:
    mapping = {
        "full": texts.ERROR_SERVER_FULL,
        "disabled": texts.ERROR_SERVER_DISABLED,
        "busy": texts.ERROR_SERVER_BUSY,
        "slots_unknown": texts.ERROR_SERVER_SLOTS_UNKNOWN,
        "api_failed": texts.ERROR_API_CREATE_FAILED,
        "db_error": texts.ERROR_TECHNICAL_MESSAGE,
        "unknown": texts.ERROR_SERVER_UNAVAILABLE,
    }
    return mapping.get(error_type, texts.ERROR_SERVER_UNAVAILABLE)


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
    return
