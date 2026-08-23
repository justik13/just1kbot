"""Durable notification coordinator for Telegram outbox events.

Guarantees:
1. At-least-once delivery with durable recovery.
2. Zero database locks held during Telegram Bot API network I/O.
3. Automatic compensation (deletion of sent message) if checkout is abandoned during send.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.connection import session_scope
from database.models import Payment, PaymentNotification, User
from database.repositories.tariff_quotes_repo import lock_checkout_user
from database.repositories.users_repo import mark_user_bot_blocked
from utils.datetime_helpers import now_utc

logger = logging.getLogger(__name__)

NOTIFICATION_LEASE_SECONDS = 30


@dataclass(frozen=True)
class NotificationClaim:
    notification_id: int
    payment_id: int
    user_id: int
    chat_id: int
    kind: str
    state: str
    claim_token: str
    attempt_number: int
    payload: dict[str, Any]
    telegram_message_id: int | None = None


async def ensure_payment_notification(
    session: AsyncSession,
    *,
    payment_id: int,
    kind: str,
    chat_id: int,
    payload_snapshot: dict[str, Any] | None = None,
) -> PaymentNotification:
    """Ensure a durable outbox record exists for a payment notification."""
    notif = await session.scalar(
        select(PaymentNotification).where(
            PaymentNotification.payment_id == payment_id,
            PaymentNotification.kind == kind,
        )
    )
    if notif is None:
        notif = PaymentNotification(
            payment_id=payment_id,
            kind=kind,
            chat_id=chat_id,
            payload_snapshot=payload_snapshot or {},
            state="pending",
            claim_until=now_utc(),
        )
        session.add(notif)
        await session.flush()
    return notif


async def claim_notification(
    session: AsyncSession,
    *,
    worker_id: str,
    lease_seconds: int = NOTIFICATION_LEASE_SECONDS,
) -> NotificationClaim | None:
    """Claim a single pending notification for presentation."""
    now = now_utc()
    # Step 1: Discover candidate row
    row = await session.scalar(
        select(PaymentNotification)
        .where(
            (PaymentNotification.state == "pending")
            | (
                (PaymentNotification.state == "claimed")
                & (PaymentNotification.claim_until < now)
            )
            | (
                (PaymentNotification.state == "compensation_retryable")
                & (PaymentNotification.claim_until < now)
            )
        )
        .order_by(PaymentNotification.id.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if row is None:
        return None

    if row.attempts >= row.max_attempts:
        row.state = "dead"
        row.last_error = "max_attempts_exceeded"
        await session.flush()
        return None

    claim_token = uuid.uuid4().hex
    row.state = "claimed" if row.state != "compensation_retryable" else "compensation_required"
    row.claim_token = claim_token
    row.claim_until = now + timedelta(seconds=lease_seconds)
    row.attempts += 1
    await session.flush()

    payment = await session.get(Payment, row.payment_id)
    user_id = payment.user_id if payment else 0

    return NotificationClaim(
        notification_id=row.id,
        payment_id=row.payment_id,
        user_id=user_id,
        chat_id=row.chat_id,
        kind=row.kind,
        state=row.state,
        claim_token=claim_token,
        attempt_number=row.attempts,
        payload=row.payload_snapshot or {},
        telegram_message_id=row.telegram_message_id,
    )


async def execute_notification_presentation(
    bot: Bot,
    claim: NotificationClaim,
    *,
    render_func,
) -> bool:
    """Execute 4-phase non-blocking notification delivery with automatic compensation."""
    # If this is a compensation retry, jump straight to Phase 4 (cleanup)
    if claim.state in ("compensation_required", "compensation_retryable"):
        return await _execute_compensation_phase(bot, claim)

    # Phase 1: Pre-send verification under lock
    async with session_scope() as session:
        if claim.user_id:
            await lock_checkout_user(session, claim.user_id)
        p = await session.get(Payment, claim.payment_id)
        if p is None or p.checkout_status != "active" or not p.ui_visible:
            # Cancelled before send started -> mark compensated/dead
            notif = await session.get(PaymentNotification, claim.notification_id)
            if notif and notif.claim_token == claim.claim_token:
                notif.state = "compensated"
                notif.last_error = "cancelled_before_send"
            return True

    # Phase 2: Lock-Free External Telegram I/O
    new_msg_id: int | None = None
    telegram_error: str | None = None
    try:
        new_msg_id = await render_func(bot, claim.chat_id, claim.payload)
    except TelegramForbiddenError:
        async with session_scope() as session:
            if claim.user_id:
                user = await session.get(User, claim.user_id)
                if user:
                    await mark_user_bot_blocked(session, user.telegram_id)
            notif = await session.get(PaymentNotification, claim.notification_id)
            if notif and notif.claim_token == claim.claim_token:
                notif.state = "dead"
                notif.last_error = "bot_blocked"
        return True
    except Exception as exc:  # noqa: BLE001
        telegram_error = str(exc)
        logger.warning(
            "Telegram notification send failed for payment %s: %s",
            claim.payment_id,
            exc,
        )
        async with session_scope() as session:
            notif = await session.get(PaymentNotification, claim.notification_id)
            if notif and notif.claim_token == claim.claim_token:
                notif.state = "pending"
                notif.last_error = telegram_error[:250]
                notif.claim_until = now_utc() + timedelta(seconds=10)
        return False

    # Phase 3 & 4: Shielded Verification, Acknowledge & Compensation TX
    async def _ack_and_compensate() -> bool:
        needs_compensation = False
        async with session_scope() as session:
            if claim.user_id:
                await lock_checkout_user(session, claim.user_id)
            p = await session.get(Payment, claim.payment_id)
            notif = await session.get(PaymentNotification, claim.notification_id)

            if notif is None or notif.claim_token != claim.claim_token:
                # Lease expired and stolen by another worker
                needs_compensation = True
            elif p is None or p.checkout_status != "active" or not p.ui_visible:
                # Concurrent cancellation occurred while Telegram I/O was in-flight!
                notif.state = "compensation_required"
                notif.telegram_message_id = new_msg_id
                needs_compensation = True
            else:
                # Success!
                notif.state = "delivered"
                notif.telegram_message_id = new_msg_id
                if claim.kind == "payment_url":
                    p.payment_url_notified_at = now_utc()
                    p.topup_context = {
                        **(p.topup_context or {}),
                        "message_id": new_msg_id,
                        "auto_show": False,
                    }
                elif claim.kind == "balance_credit":
                    p.credit_notified_at = now_utc()

        # Phase 4: Lock-Free Compensation I/O
        if needs_compensation and new_msg_id:
            from utils.telegram import _delete_hub_messages
            del_ok = False
            try:
                await _delete_hub_messages(bot, claim.chat_id, [new_msg_id])
                del_ok = True
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to compensate orphan message %s: %s", new_msg_id, exc)

            async with session_scope() as session:
                notif = await session.get(PaymentNotification, claim.notification_id)
                if notif and notif.claim_token == claim.claim_token:
                    notif.state = "compensated" if del_ok else "compensation_retryable"
                    if not del_ok:
                        notif.claim_until = now_utc() + timedelta(seconds=15)
        return True

    return await asyncio.shield(_ack_and_compensate())


async def _execute_compensation_phase(bot: Bot, claim: NotificationClaim) -> bool:
    """Execute compensation cleanup for a stolen/abandoned notification."""
    if not claim.telegram_message_id:
        async with session_scope() as session:
            notif = await session.get(PaymentNotification, claim.notification_id)
            if notif:
                notif.state = "compensated"
        return True

    from utils.telegram import _delete_hub_messages
    del_ok = False
    try:
        await _delete_hub_messages(bot, claim.chat_id, [claim.telegram_message_id])
        del_ok = True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to delete compensated message %s: %s", claim.telegram_message_id, exc)

    async with session_scope() as session:
        notif = await session.get(PaymentNotification, claim.notification_id)
        if notif and notif.claim_token == claim.claim_token:
            notif.state = "compensated" if del_ok else "compensation_retryable"
            if not del_ok:
                notif.claim_until = now_utc() + timedelta(seconds=30)
    return del_ok
