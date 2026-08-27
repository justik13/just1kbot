import logging

from sqlalchemy.ext.asyncio import AsyncSession

from database.repositories.maintenance_repo import (
    get_maintenance_mode,
    is_maintenance_enabled,
    set_maintenance_mode,
)
from utils.admin import is_admin

logger = logging.getLogger(__name__)


class MaintenanceService:
    """Service for persisting and evaluating maintenance mode state."""

    @staticmethod
    async def is_enabled(session: AsyncSession) -> bool:
        return await is_maintenance_enabled(session)

    @staticmethod
    async def get_message(session: AsyncSession) -> str | None:
        maintenance = await get_maintenance_mode(session)
        return maintenance.message if maintenance is not None else None

    @staticmethod
    async def can_user_perform_action(
        session: AsyncSession,
        telegram_id: int,
    ) -> bool:
        """Return True when the user may perform maintenance-restricted actions."""
        if is_admin(telegram_id):
            return True
        return not await is_maintenance_enabled(session)

    @staticmethod
    async def enable(
        session: AsyncSession,
        admin_id: int,
        message: str | None = None,
    ) -> None:
        await set_maintenance_mode(
            session,
            is_enabled=True,
            message=message,
            updated_by=admin_id,
        )
        logger.info("Maintenance mode enabled by admin %s", admin_id)

    @staticmethod
    async def disable(
        session: AsyncSession,
        admin_id: int,
    ) -> None:
        await set_maintenance_mode(
            session,
            is_enabled=False,
            updated_by=admin_id,
        )
        logger.info("Maintenance mode disabled by admin %s", admin_id)

    @staticmethod
    async def toggle(
        session: AsyncSession,
        admin_id: int,
        message: str | None = None,
    ) -> bool:
        """Toggle maintenance mode and return its new state."""
        current = await is_maintenance_enabled(session)

        if current:
            await MaintenanceService.disable(session, admin_id)
            return False

        await MaintenanceService.enable(session, admin_id, message=message)
        return True
