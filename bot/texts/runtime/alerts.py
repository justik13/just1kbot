"""Domain texts for runtime/alerts.py."""
from __future__ import annotations

ALERT_CRITICAL_BOT_ERROR = """🚨 <b>Критическая ошибка бота</b>
<b>Тип:</b> <code>{error_type}</code>
<b>Request ID:</b> <code>{request_id}</code>
<b>Детали:</b> <code>{error_short}</code>"""

ALERT_SERVER_PROBLEM = """⚠️ <b>Проблема со связью с сервером</b>

🌍 Сервер: <b>{server_name}</b> (ID: {server_id})
API не отвечает после повторной проверки.

Возможна недоступность или нестабильное соединение.

🔍 <b>Проверьте сервер.</b>
Автоматический мониторинг продолжается."""

ALERT_SERVER_RESTORED = """✅ <b>Связь с сервером восстановлена</b>

🌍 Сервер: <b>{server_name}</b> (ID: {server_id})
API снова стабильно доступен."""

ALERT_SERVER_DISK_CRITICAL = """⚠️ <b>Диск сервера заполнен более чем на 85%</b>

Сервер: <b>{server_name}</b> (ID: {server_id})
Использование диска: <b>{disk_percent:.1f}%</b>
Очистите логи или расширьте диск."""

ALERT_SERVER_HOSTING_TRAFFIC_WARN = "⚠️ Сервер {server_name} израсходовал 80% лимита хостинга ({used_gb} / {limit_gb} ГБ)"
ALERT_SERVER_HOSTING_TRAFFIC_CRITICAL = "🚨 Сервер {server_name} достиг 90% лимита хостинга ({used_gb} / {limit_gb} ГБ) и автоматически отключён в боте"
ALERT_SERVER_HOSTING_TRAFFIC_RESTORED = "✅ Сервер {server_name} автоматически включён в новом расчётном периоде ({cycle})"

ALERT_TITLE_CRITICAL_STOP = "Критическая остановка фоновых задач"

ALERT_TITLE_WORKER_FAILED = "Фоновый воркер упал"

ALERT_TRAFFIC_OVERUSAGE = """⚠️ <b>Fair Usage Policy: превышение 1 TiB за месяц</b>
━━━━━━━━━━━━━━━━━━━━
👤 <b>Пользователь:</b> <code>{telegram_id}</code>
📅 <b>Период:</b> {cycle}
📊 <b>Всего за месяц:</b> <b>{total_traffic}</b>
   └ <b>Стандартный доступ:</b> {vless_traffic}
━━━━━━━━━━━━━━━━━━━━
<i>Доступ не заблокирован. Рекомендуется проверить активность пользователя.</i>"""

ALERT_WORKER_CRASH = """🚨 <b>{title}</b>
🧩 <b>Воркер:</b> <code>{worker}</code>
🔁 <b>Падений:</b> {failure_count}
⚠️ <b>Тип ошибки:</b> <code>{error_type}</code>"""

ALERT_INGRESS_PROBLEM = """⚠️ <b>Проблема с проксированием подписок (Ingress / Nginx)</b>

🌍 Сервер: <b>{server_name}</b> (ID: {server_id})
🌐 Домен: <code>{domain}</code>
Ошибка: Эндпоинт <code>{endpoint}</code> вернул статус {status_or_err}.

⚠️ Узел Xray работает в штатном режиме, но выдача подписок через Nginx может быть недоступна (ошибка 502 / SSL).
🔍 <b>Проверьте настройки Nginx и сертификаты на шлюзе (ru_hub).</b>"""

ALERT_INGRESS_RESTORED = """✅ <b>Проксирование подписок восстановлено</b>

🌍 Сервер: <b>{server_name}</b> (ID: {server_id})
🌐 Домен: <code>{domain}</code>
Эндпоинт <code>{endpoint}</code> снова отвечает 200 OK."""

ALERT_INGRESS_ERR_TIMEOUT = "Timeout (таймаут соединения)"

ALERT_CDN_INGRESS_PROBLEM = """⚠️ <b>Проблема с доставкой подписок через CDN (Яндекс Облако)</b>

🌍 Сервер: <b>{server_name}</b> (ID: {server_id})
🌐 CDN Домен: <code>{domain}</code> (шлюз: <code>{origin_domain}</code>)
Ошибка: Эндпоинт <code>{endpoint}</code> через CDN вернул статус {status_or_err}.

ℹ️ <i>Nginx на шлюзе доступен (200 OK), но CDN стабильно не доставляет запросы.</i>
🔍 <b>Проверьте баланс Яндекс Облака, сертификат в Certificate Manager и статус CDN-ресурса.</b>"""

ALERT_CDN_INGRESS_RESTORED = """✅ <b>Доставка подписок через CDN восстановлена</b>

🌍 Сервер: <b>{server_name}</b> (ID: {server_id})
🌐 CDN Домен: <code>{domain}</code>
Эндпоинт <code>{endpoint}</code> через CDN снова отвечает 200 OK."""

ALERT_SERVER_OUTAGE_REMINDER = """⚠️ <b>Напоминание: Связь отсутствует уже {hours_text}</b>

🌍 Сервер: <b>{server_name}</b> (ID: {server_id})
Причина: API недоступен / соединение нестабильно.

🔍 <i>Проверьте доступность узла или запустите диагностику 'just1knode doctor'.</i>"""

ALERT_INGRESS_PROBLEM_REMINDER = """⚠️ <b>Напоминание: Сбой проксирования подписок продолжается ({hours_text})</b>

🌍 Сервер: <b>{server_name}</b> (ID: {server_id})
🌐 Домен: <code>{domain}</code>
Эндпоинт <code>{endpoint}</code> по-прежнему недоступен.

🔍 <i>Проверьте Nginx и SSL-сертификаты на шлюзе (ru_hub).</i>"""

ALERT_CDN_INGRESS_PROBLEM_REMINDER = """⚠️ <b>Напоминание: Сбой доставки подписок через CDN продолжается ({hours_text})</b>

🌍 Сервер: <b>{server_name}</b> (ID: {server_id})
🌐 CDN Домен: <code>{domain}</code>
Эндпоинт <code>{endpoint}</code> через CDN по-прежнему не отвечает.

🔍 <i>Проверьте Яндекс Облако (баланс, сертификат и статус CDN).</i>"""


ALERT_WHITE_INTERNET_HUB_EXHAUSTED = """🚨 <b>Нет доступных шлюзов (ru_hub)</b>

Не удалось выделить шлюз (ru_hub) для подписки ID: <code>{sub_id}</code>.
Все шлюзы либо недоступны (сбой связи / отключены), либо недоступны для выдачи.

🔍 <i>Проверьте доступность ru_hub в админке.</i>"""

ALERT_WHITE_INTERNET_HUB_RESTORED = """✅ <b>Шлюз (ru_hub) снова доступен для распределения</b>

Выделение узлов для новых подписок возобновлено в штатном режиме."""

# Backward aliases
ALERT_WHITE_INTERNET_ORIGIN_EXHAUSTED = ALERT_WHITE_INTERNET_HUB_EXHAUSTED
ALERT_WHITE_INTERNET_ORIGIN_RESTORED = ALERT_WHITE_INTERNET_HUB_RESTORED

ALERT_TITLE_WORKER_COOLDOWN = "Воркер превысил лимит ошибок и перешёл в режим ожидания (15 мин)"

ALERT_WORKER_COOLDOWN = """🚨 <b>{title}</b>
🧩 <b>Воркер:</b> <code>{worker}</code>
🔁 <b>Ошибок подряд:</b> {failure_count}
⚠️ <b>Последняя ошибка:</b> <code>{error_type}</code>

Фоновая задача приостановлена на 15 минут для предотвращения лавины ошибок."""

ALERT_BRIDGE_TSPU_PROBLEM = """⚠️ <b>Нарушена связность между шлюзами (РФ ↔ ЕС)</b>

🇷🇺 Входной шлюз: <b>{ru_name}</b> (ID: {ru_id})
🇪🇺 Внешний шлюз: <b>{eu_name}</b> (ID: {eu_id}, <code>{eu_ip}</code>)
Причина: <code>{error_detail}</code>

ℹ️ <i>Внешний шлюз доступен со стороны бота, но с российского шлюза не проходит контрольная передача данных (вероятна сетевая фильтрация на маршруте или ограничение IP).</i>
🔀 {action_text}"""

ALERT_BRIDGE_TSPU_PROBLEM_REMINDER = """⚠️ <b>Напоминание: Сбой маршрута {ru_name} → {eu_name} продолжается ({hours_text})</b>

🇷🇺 Входной шлюз: <b>{ru_name}</b> (ID: {ru_id})
🇪🇺 Внешний шлюз: <b>{eu_name}</b> (ID: {eu_id}, <code>{eu_ip}</code>)
Причина: <code>{error_detail}</code>"""

ALERT_BRIDGE_TSPU_RESTORED = """✅ <b>Связность между шлюзами восстановлена</b>

🇷🇺 Входной шлюз: <b>{ru_name}</b> (ID: {ru_id})
🇪🇺 Внешний шлюз: <b>{eu_name}</b> (ID: {eu_id}, <code>{eu_ip}</code>)
Маршрут снова стабильно пропускает трафик и возвращён в рабочую топологию."""

ALERT_SERVER_LAST_APPLY_FAILED = """⚠️ <b>Ошибка применения конфигурации на сервере</b>

🖥 <b>Сервер:</b> {server_name} (ID: {server_id})
⏰ <b>Когда:</b> {applied_at}
📄 <b>Детали:</b> {error_text}"""

ALERT_SERVER_SSL_CERT_EXPIRING = """⚠️ <b>Скоро истекает TLS-сертификат сервера</b>

🖥 <b>Сервер:</b> {server_name} (ID: {server_id})
📅 <b>Осталось дней:</b> {days_left}
⏳ <b>Действует до:</b> {expires_at}"""





