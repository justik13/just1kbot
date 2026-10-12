"""Proactive push for credited orders on the request path.

Used by the YooKassa webhook: the user may already be elsewhere in the bot,
so the push is a new message (``force_new=True``), not a screen update.

Returns ``True`` only when the push was actually delivered; any failure
(including a missing ``telegram_id``) returns ``False`` so the caller keeps
the notification debt for the credit-notify worker.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Order, User

logger = logging.getLogger(__name__)


def build_tariff_success_card(order: Order, balance):
    """Build the (text, keyboard) purchase-success card for a tariff order.

    Single source of truth for the card previously copy-pasted across the
    order_check renders and the webhook push: tier label, tariff-change
    title and balance figures must never drift between delivery paths.
    """
    from bot import texts
    from bot.formatters import get_tariff_display_name
    from bot.keyboards import get_payment_success_keyboard

    tariff_name = get_tariff_display_name(order.device_limit or 2)
    is_change = bool(order.metadata_ and order.metadata_.get("is_tariff_change"))
    operation = (
        texts.PAYMENT_OP_TITLE_CHANGE if is_change else texts.PURCHASE_COMPLETED
    )
    return (
        texts.PAYMENT_PURCHASE_SUCCESS_CARD.format(
            operation_title=operation,
            tariff_name=tariff_name,
            duration_days=order.duration_days,
            charged=int(order.amount_rub),
            real_balance=int(balance.real_available),
            bonus_balance=int(balance.bonus_available),
        ),
        get_payment_success_keyboard(),
    )


async def notify_order_credited(
    bot,
    session: AsyncSession,
    order: Order,
    user: User | None,
) -> bool:
    """Send the credited/success card for a settled order."""
    from bot import texts
    from database.repositories.account_ledger_repo import get_account_balance
    from utils.telegram import EFFECT_CONFETTI, render_hub

    try:
        if not user or not user.telegram_id:
            return False
        if order.service_type == "topup":
            from bot.handlers.payment.balance_routes import _render_balance
            from bot.keyboards.payment import get_topup_credit_keyboard

            order_context = (
                (order.metadata_ or {}).get("context") if order.metadata_ else None
            )
            kb = get_topup_credit_keyboard(order_context) if order_context else None
            await _render_balance(
                bot,
                user.telegram_id,
                session,
                user,
                notice=texts.TOPUP_CREDITED_NOTICE,
                message_effect_id=EFFECT_CONFETTI,
                force_new=True,
                custom_keyboard=kb,
            )
        else:
            from database.repositories.account_ledger_repo import get_account_balance
            from utils.telegram import render_hub

            balance = await get_account_balance(session, user_id=user.id)
            text, keyboard = build_tariff_success_card(order, balance)
            await render_hub(
                bot,
                user.telegram_id,
                text,
                keyboard,
                message_effect_id=EFFECT_CONFETTI,
                force_new=True,
            )
    except Exception as exc:
        logger.warning(
            "Could not notify user %s of credited order %s: %s",
            getattr(user, "id", "?"),
            getattr(order, "id", "?"),
            exc,
        )
        return False
    return True
