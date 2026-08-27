"""Domain texts for admin/broadcast.py."""
from __future__ import annotations

ADMIN_BROADCAST = "Всего"

ADMIN_BROADCAST_AUDIENCE_LABELS = {'all': 'Все пользователи', 'active': 'Активные подписки', 'expiring_3d': 'Подписки истекают < 3 дней', 'expired': 'Истекшие подписки', 'never': 'Без подписок', 'test': 'Тестовая отправка админу'}

ADMIN_BROADCAST_PROGRESS_AUDIENCE_LABELS = {'all': 'Все пользователи', 'active': 'Активные подписки', 'expiring_3d': '⏳ Истекают < 3 дней', 'expired': '🔴 Истекшие подписки', 'never': '🆕 Без подписок'}

ADMIN_BROADCAST_RECIPIENTS_COUNT = "⚠️ Текст рассылки слишком длинный. Максимум {value_0} символов."

ADMIN_BROADCAST_SENDING_PROGRESS = """🚨 <b>Рассылка остановлена из-за ошибки</b>
<code>{value_0}</code>"""

ADMIN_BROADCAST_STOPPED_NOTICE = "✅ Ок (Убрать)"

ADMIN_BROADCAST_SUMMARY_RESULT = "Рассылка не запущена"

ADMIN_BROADCAST_TEST_SENT_SUCCESS = "⚠️ Подпись к медиа слишком длинная. Максимум {value_0} символов."

BROADCAST_ACTIVE_LABEL = "Активных"

BROADCAST_ALREADY_RUNNING = "⏳ Рассылка уже идёт, дождитесь завершения"

BROADCAST_AUDIENCE = """👥 <b>Аудитория:</b> {aud_label}
"""

ADMIN_BROADCAST_TITLE_BROADCAST = "📢 Управление рассылками"
ADMIN_BROADCAST_VVOD_TEXTA_RASSYLKI = "📝 Введите текст для рассылки"

ADMIN_AUDIT_LOG_DETAILS_BROADCAST = "to {label}: {success} success, {fail} fail, status={status}"

BROADCAST_BROADCAST = "📢 Рассылка"

BROADCAST_NO_RECIPIENTS = "⚠️ Нет получателей для рассылки"

BROADCAST_PREVIEW_S_PREVIEW_VY = "Ознакомьтесь с предпросмотром выше и подтвердите запуск рассылки."

BROADCAST_PROMPT = """🛠 Админка › 📢 <b>Рассылка</b>

📢 Введите текст сообщения для рассылки:

Поддерживается HTML-разметка (<b>жирный</b>, <i>курсив</i>, <code>код</code>)"""

BROADCAST_RECIPIENTS_CHEL = """📊 <b>Получателей:</b> {total_count} чел.

"""

BROADCAST_RESULT = """✅ Рассылка завершена!

📤 Отправлено: {success_count}
❌ Ошибок: {fail_count}
👥 {label}: {total_count}"""

BROADCAST_SELECT_AUDITORIYU_FOR_RASSY = """{header}<b>Выберите аудиторию для рассылки:</b>

Кому отправить сообщение?"""

BROADCAST_STARTED = """🚀 <b>Рассылка запущена!</b>

Отправляю {total_count} пользователям...

Результат придёт отдельным сообщением."""

BROADCAST_STEP_1_SELECT_AUDIENCE = "Шаг 1: Выбор аудитории"

BROADCAST_STOPPING = "⏹ Рассылка останавливается..."

BROADCAST_TEST_MESSAGE_OTPRAVLE = """✅ <b>Тестовое сообщение отправлено вам для проверки!</b>

"""

BROADCAST_TEST_MNE_ADMINU = "🧪 Тест мне (Админу)"
