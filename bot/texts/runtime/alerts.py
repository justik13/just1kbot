"""Domain texts for runtime/alerts.py."""
from __future__ import annotations

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

ALERT_INGRESS_PROBLEM = """⚠️ <b>Проблема с проксированием подписок (Ingress / Nginx)</b>

🌍 Сервер: <b>{server_name}</b> (ID: {server_id})
🌐 Домен: <code>{domain}</code>
Ошибка: Эндпоинт <code>{endpoint}</code> вернул статус {status_or_err}.

⚠️ Узел Xray работает в штатном режиме, но выдача подписок через Nginx может быть недоступна (ошибка 502 / SSL).
🔍 <b>Проверьте настройки Nginx и сертификаты на Origin.</b>"""

ALERT_INGRESS_RESTORED = """✅ <b>Проксирование подписок восстановлено</b>

🌍 Сервер: <b>{server_name}</b> (ID: {server_id})
🌐 Домен: <code>{domain}</code>
Эндпоинт <code>{endpoint}</code> снова отвечает 200 OK."""

ALERT_INGRESS_ERR_TIMEOUT = "Timeout (таймаут соединения)"

ALERT_CDN_INGRESS_PROBLEM = """⚠️ <b>Проблема с доставкой подписок через CDN (Яндекс Облако)</b>

🌍 Сервер: <b>{server_name}</b> (ID: {server_id})
🌐 CDN Домен: <code>{domain}</code> (Origin: <code>{origin_domain}</code>)
Ошибка: Эндпоинт <code>{endpoint}</code> через CDN вернул статус {status_or_err}.

ℹ️ <i>Origin Nginx доступен (200 OK), но CDN стабильно не доставляет запросы.</i>
🔍 <b>Проверьте баланс Яндекс Облака, сертификат в Certificate Manager и статус CDN-ресурса.</b>"""

ALERT_CDN_INGRESS_RESTORED = """✅ <b>Доставка подписок через CDN восстановлена</b>

🌍 Сервер: <b>{server_name}</b> (ID: {server_id})
🌐 CDN Домен: <code>{domain}</code>
Эндпоинт <code>{endpoint}</code> через CDN снова отвечает 200 OK."""




