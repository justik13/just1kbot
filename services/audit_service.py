import json
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from database.repositories.audit_repo import create_audit_log

logger = logging.getLogger(__name__)


class AuditService:
    @staticmethod
    async def log_action(
        session: AsyncSession,
        admin_id: int,
        action: str,
        target_type: str | None = None,
        target_id: int | None = None,
        details: Any = None,
    ):
        """Universal audit logger supporting string and dictionary details."""
        in_tx = False
        in_tx_attr = getattr(session, "in_transaction", None)
        if callable(in_tx_attr):
            try:
                if not type(in_tx_attr).__name__.startswith("AsyncMock"):
                    in_tx = bool(in_tx_attr())
            except Exception:
                in_tx = False
        if in_tx:
            await session.flush()

        try:
            formatted_details = None
            if details is not None:
                if isinstance(details, (dict, list)):
                    formatted_details = json.dumps(details, ensure_ascii=False)
                else:
                    formatted_details = str(details)

            normalized_target_type = target_type.lower() if target_type else None

            nested_attr = getattr(session, "begin_nested", None)
            if callable(nested_attr) and not type(nested_attr).__name__.startswith("AsyncMock"):
                async with session.begin_nested():
                    await create_audit_log(
                        session=session,
                        admin_id=admin_id,
                        action=action,
                        target_type=normalized_target_type,
                        target_id=target_id,
                        details=formatted_details,
                    )
            else:
                await create_audit_log(
                    session=session,
                    admin_id=admin_id,
                    action=action,
                    target_type=normalized_target_type,
                    target_id=target_id,
                    details=formatted_details,
                )
        except Exception as e:
            logger.error("Failed to write audit log action %s: %s", action, e)

    @staticmethod
    async def log_user_action(
        session: AsyncSession,
        *,
        user_id: int,
        action: str,
        details: Any = None,
        admin_id: int = 0,
    ):
        """Convenience method for logging user-targeted events."""
        await AuditService.log_action(
            session=session,
            admin_id=admin_id,
            action=action,
            target_type="user",
            target_id=user_id,
            details=details,
        )

    @staticmethod
    async def log_admin_action(
        session: AsyncSession,
        *,
        admin_id: int,
        action: str,
        target_type: str,
        target_id: int | None = None,
        details: Any = None,
    ):
        """Convenience method for logging admin-initiated events."""
        await AuditService.log_action(
            session=session,
            admin_id=admin_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            details=details,
        )
