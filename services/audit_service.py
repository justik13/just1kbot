import json
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from config.enums import AdminAuditAction
from database.repositories.audit_repo import create_audit_log

logger = logging.getLogger(__name__)


class AuditService:
    @staticmethod
    async def log_action(
        session: AsyncSession,
        admin_id: int,
        action: str | AdminAuditAction,
        target_type: str | None = None,
        target_id: int | None = None,
        details: Any = None,
    ):
        """Universal audit logger supporting string and dictionary details."""
        if hasattr(session, "in_transaction") and session.in_transaction():
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
            if isinstance(action, AdminAuditAction):
                action_val = action.value
            elif isinstance(action, str):
                try:
                    action_val = AdminAuditAction(action).value
                except ValueError:
                    logger.warning(
                        "Audit action '%s' is not defined in canonical AdminAuditAction enum.",
                        action,
                    )
                    action_val = action
            else:
                action_val = str(action)

            if hasattr(session, "begin_nested"):
                async with session.begin_nested():
                    await create_audit_log(
                        session=session,
                        admin_id=admin_id,
                        action=action_val,
                        target_type=normalized_target_type,
                        target_id=target_id,
                        details=formatted_details,
                    )
                return

            await create_audit_log(
                session=session,
                admin_id=admin_id,
                action=action_val,
                target_type=normalized_target_type,
                target_id=target_id,
                details=formatted_details,
            )
        except Exception as e:
            logger.error("Failed to write audit log action %s: %s", action, e)
