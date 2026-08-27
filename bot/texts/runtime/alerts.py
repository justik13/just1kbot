"""Domain texts for runtime/alerts.py."""
from __future__ import annotations

ALERT_BALANCE_LIMIT_EXCEEDED = """⚠️ <b>ВНИМАНИЕ: Превышен лимит баланса!</b>

Платёж #{payment_id}, пользователь {telegram_id}
Позиция: {real_position} ₽"""

ALERT_CRITICAL_BOT_ERROR = """🚨 <b>Критическая ошибка бота</b>
<b>Тип:</b> <code>{error_type}</code>
<b>Request ID:</b> <code>{request_id}</code>
<b>Детали:</b> <code>{error_short}</code>"""

ALERT_SERVER_AUTO_DISABLED = """🔴 <b>Сервер автоматически отключён</b>

🌍 Сервер: <b>{server_name}</b> (ID: {server_id})
Сервер не восстановил стабильное соединение в течение 15 минут.

Причина: API недоступен / соединение нестабильно.
Сервер исключён из работы.

🔕 Повторных уведомлений не будет.
Доступность будет проверяться автоматически каждые 15 минут."""

ALERT_SERVER_AUTO_DISABLED_RECOVERED = """✅ <b>Сервер восстановлен</b>

🌍 Сервер: <b>{server_name}</b> (ID: {server_id})
API стабильно отвечает.

Сервер остаётся отключённым. При необходимости включите его вручную."""

ALERT_SERVER_DISK_CRITICAL = """⚠️ <b>ВНИМАНИЕ: Диск VPN-ноды забит > 85%!</b>

Сервер: <b>{server_name}</b> (ID: {server_id})
Использование диска: <b>{disk_percent:.1f}%</b>
Рекомендуется очистить логи или расширить диск."""

ALERT_SERVER_PROBLEM = """⚠️ <b>Проблема с VPN-сервером</b>

🌍 Сервер: <b>{server_name}</b> (ID: {server_id})
API не отвечает после повторной проверки.

Возможна недоступность или нестабильное соединение.

🔍 <b>Проверьте сервер.</b>
Автоматический мониторинг продолжается."""

ALERT_SERVER_RESTORED = """✅ <b>VPN-сервер восстановлен</b>

🌍 Сервер: <b>{server_name}</b> (ID: {server_id})
API снова стабильно доступен."""

ALERT_STALE_PAYMENTS_HEADER = """⚠️ <b>Обнаружены зависшие пополнения ({count} шт.)</b>

{details}"""

ALERT_STALE_PAYMENTS_MORE = """• ...и ещё {more_count} платежей
"""

ALERT_STALE_PAYMENT_ROW = """• {icon} #{payment_id} (user {telegram_id}): {amount} {currency} via {method}
"""

ALERT_STALE_TOPUP = """⚠️ <b>Зависшие пополнения баланса:</b>

{details}"""

ALERT_TITLE_CRITICAL_STOP = "Критическая остановка фоновых задач"

ALERT_TITLE_WORKER_FAILED = "Фоновый воркер упал"

ALERT_TRAFFIC_OVERUSAGE = """⚠️ <b>Fair Usage Policy: Превышение квоты трафика!</b>
━━━━━━━━━━━━━━━━━━━━
👤 <b>Пользователь:</b> <code>{telegram_id}</code>
🖥 <b>Сервер:</b> {server_name}
📊 <b>Трафик за сутки:</b> {tib:.2f} TiB
🔑 <b>Профиль ID:</b> {profile_id}
━━━━━━━━━━━━━━━━━━━━
<i>Рекомендуется проверить активность пользователя.</i>"""

ALERT_WORKER_CRASH = """🚨 <b>{title}</b>
🧩 <b>Воркер:</b> <code>{worker}</code>
🔁 <b>Падений:</b> {failure_count}
⚠️ <b>Тип ошибки:</b> <code>{error_type}</code>"""
