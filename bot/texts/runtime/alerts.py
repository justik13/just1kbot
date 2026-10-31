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

🔔 Повторные напоминания будут приходить каждый 1 час до восстановления узла.
Доступность проверяется автоматически каждые 15 минут."""

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

ALERT_TRAFFIC_OVERUSAGE = """⚠️ <b>Fair Usage Policy: Превышение 1 TiB за месяц!</b>
━━━━━━━━━━━━━━━━━━━━
👤 <b>Пользователь:</b> <code>{telegram_id}</code>
📅 <b>Период:</b> {cycle}
📊 <b>Всего за месяц:</b> <b>{total_traffic}</b>
   ├ <b>AmneziaWG:</b> {awg_traffic}
   └ <b>VLESS:</b> {vless_traffic}
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

ALERT_RELAY_TSPU_PROBLEM = """⚠️ <b>Сбой доступности узла из РФ (возможно ТСПУ)</b>

🌍 Сервер: <b>{relay_name}</b> (<code>{relay_ip}:{relay_port}</code>)
📡 Контрольный узел: <b>{origin_name}</b> (Москва, ID: {origin_id})
⚠️ Ошибка проверки: <code>{status_or_err}</code>

Сетевой узел стабильно не отвечает на TCP-подключение со стороны Origin в Москве.
Возможна трансграничная фильтрация, сбой магистрального провайдера или блокировка IP на оборудовании ТСПУ."""

ALERT_RELAY_TSPU_RESTORED = """✅ <b>Доступность узла из РФ восстановлена</b>

🌍 Сервер: <b>{relay_name}</b> (<code>{relay_ip}:{relay_port}</code>)
📡 Контрольный узел: <b>{origin_name}</b> (Москва, ID: {origin_id})
⏱ RTT соединения: <b>{rtt_ms} мс</b>

TCP-соединение из Москвы снова успешно устанавливается."""

ALERT_SERVER_OUTAGE_REMINDER = """⚠️ <b>Напоминание: Сервер отключён уже {hours_text}</b>

🌍 Сервер: <b>{server_name}</b> (ID: {server_id})
Причина: API недоступен / соединение нестабильно.
Сервер по-прежнему исключён из распределения трафика.

🔍 <i>Проверьте доступность узла или запустите диагностику 'just1knode doctor'.</i>"""

ALERT_INGRESS_PROBLEM_REMINDER = """⚠️ <b>Напоминание: Сбой проксирования подписок продолжается ({hours_text})</b>

🌍 Сервер: <b>{server_name}</b> (ID: {server_id})
🌐 Домен: <code>{domain}</code>
Эндпоинт <code>{endpoint}</code> по-прежнему недоступен.

🔍 <i>Проверьте Nginx и SSL-сертификаты на Origin.</i>"""

ALERT_CDN_INGRESS_PROBLEM_REMINDER = """⚠️ <b>Напоминание: Сбой доставки подписок через CDN продолжается ({hours_text})</b>

🌍 Сервер: <b>{server_name}</b> (ID: {server_id})
🌐 CDN Домен: <code>{domain}</code>
Эндпоинт <code>{endpoint}</code> через CDN по-прежнему не отвечает.

🔍 <i>Проверьте Яндекс Облако (баланс, сертификат и статус CDN).</i>"""

ALERT_RELAY_TSPU_PROBLEM_REMINDER = """⚠️ <b>Напоминание: Сбой доступности узла из РФ продолжается ({hours_text})</b>

🌍 Сервер: <b>{relay_name}</b> (<code>{relay_ip}:{relay_port}</code>)
📡 Контрольный узел: <b>{origin_name}</b> (Москва, ID: {origin_id})

Сетевой узел по-прежнему не отвечает на TCP-подключение со стороны Origin в Москве."""

ALERT_API_OPERATION_DEAD = """🚨 <b>Сбой операции с ключом / пиром</b>

⚙️ <b>Тип:</b> <code>{operation_type}</code> (ID: {operation_id})
👤 <b>Профиль ID:</b> <code>{profile_id}</code>
🌍 <b>Сервер:</b> <b>{server_name}</b> (ID: {server_id})
⚠️ <b>Код ошибки:</b> <code>{error_code}</code>
📝 <b>Ошибка:</b> <code>{error_message}</code>
🔁 <b>Попыток:</b> {attempts}

Операция исчерпала лимит попыток и переведена в статус DEAD."""

ALERT_WHITE_INTERNET_ORIGIN_EXHAUSTED = """🚨 <b>Внимание: Нет доступных Origin-серверов!</b>

Не удалось выделить Origin-узел для подписки ID: <code>{sub_id}</code>.
Все Origin-серверы либо недоступны (OFFLINE/AUTO_DISABLED), либо исчерпали лимит емкости.

🔍 <i>Проверьте доступность Origin-серверов в админке или увеличьте лимит емкости (max_clients).</i>"""

ALERT_WHITE_INTERNET_ORIGIN_RESTORED = """✅ <b>Origin-сервер снова доступен для распределения</b>

Выделение узлов для новых подписок возобновлено в штатном режиме."""

ALERT_TITLE_WORKER_COOLDOWN = "Воркер превысил лимит ошибок и перешёл в режим ожидания (15 мин)"

ALERT_WORKER_COOLDOWN = """🚨 <b>{title}</b>
🧩 <b>Воркер:</b> <code>{worker}</code>
🔁 <b>Ошибок подряд:</b> {failure_count}
⚠️ <b>Последняя ошибка:</b> <code>{error_type}</code>

Фоновая задача приостановлена на 15 минут для предотвращения лавины ошибок."""





