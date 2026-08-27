"""Domain texts for runtime/alerts.py."""
from __future__ import annotations

ALERT_CRITICAL_BOT_ERROR = """🚨 <b>Критическая ошибка бота</b>
<b>Тип:</b> <code>{error_type}</code>
<b>Request ID:</b> <code>{request_id}</code>
<b>Детали:</b> <code>{error_short}</code>"""

ALERT_STALE_TOPUP = """⚠️ <b>Зависшие пополнения баланса:</b>

{details}"""
