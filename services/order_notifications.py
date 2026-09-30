"""Notification-debt flags for credited orders (pure metadata helpers).

Money arrival (fresh settlement or hold release) creates a notification debt
(``Order.metadata_['late_notify_pending']``). Three deliverers share the
contract: the YooKassa webhook (proactive push), the ``order_check`` button
(immediate render) and the credit-notify worker (backstop for users who
already left the payment screen). Whoever delivers first clears the flag.

This module is intentionally free of ``bot.*`` imports (architectural
firewall: ``services/*.py`` may import only ``bot.texts``): the actual
sending lives in ``bot/handlers/payment/credit_notify.py`` (full cards for
the request path) and ``services/workers/credit_notifications.py``
(compact push for the background path).
"""

from __future__ import annotations

from database.models import Order

LATE_NOTIFY_PENDING_KEY = "late_notify_pending"
LATE_NOTIFY_ATTEMPTS_KEY = "late_notify_attempts"


def mark_notify_pending(order: Order) -> None:
    """Record an undelivered notification debt on the order (no DB IO).

    Also drops any stale attempt counter: a new settlement (fresh or hold
    release) re-arms the full retry budget.
    """
    meta = dict(order.metadata_ or {})
    meta[LATE_NOTIFY_PENDING_KEY] = True
    meta.pop(LATE_NOTIFY_ATTEMPTS_KEY, None)
    order.metadata_ = meta


def mark_notified(order: Order) -> None:
    """Clear the notification debt after a confirmed delivery (no DB IO)."""
    meta = dict(order.metadata_ or {})
    meta.pop(LATE_NOTIFY_PENDING_KEY, None)
    meta.pop(LATE_NOTIFY_ATTEMPTS_KEY, None)
    order.metadata_ = meta
