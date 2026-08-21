import json
import logging
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession
from database.repositories.audit_repo import create_audit_log

logger = logging.getLogger(__name__)


class AuditService:
    @staticmethod
    async def log_action(
        session: AsyncSession,
        admin_id: int,
        action: str,
        target_type: Optional[str] = None,
        target_id: Optional[int] = None,
        details: Any = None,
    ):
        """Universal audit logger supporting string and dictionary details."""
        if callable(getattr(session, "in_transaction", None)) and session.in_transaction():
            # Flush pending business changes BEFORE entering the silent audit try-block
            # so that business constraint violations bubble up to the caller and aren't
            # swallowed as "audit failures", which would leave the transaction poisoned.
            await session.flush()

        try:
            formatted_details = None
            if details is not None:
                if isinstance(details, (dict, list)):
                    formatted_details = json.dumps(details, ensure_ascii=False)
                else:
                    formatted_details = str(details)

            normalized_target_type = target_type.lower() if target_type else None

            async with session.begin_nested():
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
        target_id: Optional[int] = None,
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
