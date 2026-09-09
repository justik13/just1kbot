"""Repository for admin operation idempotency tracking."""

from __future__ import annotations

import hashlib
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


def make_admin_op_key(
    action: str,
    admin_id: int,
    target_id: int,
    chat_id: int | str,
    message_id: int | str,
    value: str | int = "",
) -> str:
    """
    Generate deterministic SHA-256 idempotency key per ТЗ v6.1:
    v1|{action}|{admin_id}|{target_id}|{chat_id}|{message_id}|{value}
    """
    raw = f"v1|{action}|{admin_id}|{target_id}|{chat_id}|{message_id}|{value}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def check_and_record_admin_op(
    session: AsyncSession,
    op_key: str,
    admin_id: int,
    target_id: int,
) -> bool:
    """
    Atomically record admin operation idempotency key.

    Returns:
        True if the key was recorded (first execution).
        False if the key already exists (duplicate rejected).
    """
    stmt = text(
        """
        INSERT INTO admin_operation_idempotency (op_key, admin_id, target_id)
        VALUES (:op_key, :admin_id, :target_id)
        ON CONFLICT (op_key) DO NOTHING
        RETURNING op_key
        """
    )
    result = await session.execute(
        stmt,
        {"op_key": op_key, "admin_id": admin_id, "target_id": target_id},
    )
    inserted = result.scalar_one_or_none()
    return inserted is not None
