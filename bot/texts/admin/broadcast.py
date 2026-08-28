"""Domain texts for admin/broadcast.py."""
from __future__ import annotations
from bot.texts.admin.common import COMMON_ALL_USERS_LABEL, COMMON_ACTIVE_SUBSCRIPTIONS_LABEL
from bot.texts.common import BTN_BROADCAST_EXPIRING, BTN_BROADCAST_NO_SUB

ADMIN_BROADCAST = "Всего"


BROADCAST_AUDIENCE_LABEL_EXPIRING_3D = 'Подписки истекают менее чем за 3 дня'
BROADCAST_AUDIENCE_LABEL_EXPIRED = 'Истекшие подписки'
BROADCAST_AUDIENCE_LABEL_NEVER = 'Без подписок'
BROADCAST_AUDIENCE_LABEL_TEST = 'Тестовая отправка админу'
BROADCAST_PROGRESS_LABEL_EXPIRED = '🔴 Истекшие подписки'

ADMIN_BROADCAST_AUDIENCE_LABELS = {'all': COMMON_ALL_USERS_LABEL, 'active': COMMON_ACTIVE_SUBSCRIPTIONS_LABEL, 'expiring_3d': BROADCAST_AUDIENCE_LABEL_EXPIRING_3D, 'expired': BROADCAST_AUDIENCE_LABEL_EXPIRED, 'never': BROADCAST_AUDIENCE_LABEL_NEVER, 'test': BROADCAST_AUDIENCE_LABEL_TEST}

ADMIN_BROADCAST_PROGRESS_AUDIENCE_LABELS = {'all': COMMON_ALL_USERS_LABEL, 'active': COMMON_ACTIVE_SUBSCRIPTIONS_LABEL, 'expiring_3d': BTN_BROADCAST_EXPIRING, 'expired': BROADCAST_PROGRESS_LABEL_EXPIRED, 'never': BTN_BROADCAST_NO_SUB}

BROADCAST_ERR_TEXT_TOO_LONG = "⚠️ Текст рассылки слишком длинный. Максимум {error_summary} символов."

BROADCAST_STOPPED_ERROR_ALERT = """🚨 <b>Рассылка остановлена из-за ошибки</b>
<code>{error_summary}</code>"""

BROADCAST_BTN_DISMISS = "✅ Ок (Убрать)"

BROADCAST_NOT_STARTED_STATUS = "Рассылка не запущена"

BROADCAST_ERR_CAPTION_TOO_LONG = "⚠️ Подпись к медиа слишком длинная. Максимум {error_summary} символов."

BROADCAST_ACTIVE_LABEL = "Активных"

BROADCAST_ALREADY_RUNNING = "⏳ Рассылка уже идёт, дождитесь завершения"

BROADCAST_AUDIENCE = """👥 <b>Аудитория:</b> {aud_label}
"""

ADMIN_BROADCAST_TITLE_BROADCAST = "📢 Управление рассылками"

ADMIN_AUDIT_LOG_DETAILS_BROADCAST = "to {label}: {success} success, {fail} fail, status={status}"

BROADCAST_BROADCAST = "📢 Рассылка"

BROADCAST_NO_RECIPIENTS = "⚠️ Нет получателей для рассылки"

BROADCAST_PREVIEW_CONFIRM_HINT = "Ознакомьтесь с предпросмотром выше и подтвердите запуск рассылки."

BROADCAST_PROMPT = """🛠 Админка › 📢 <b>Рассылка</b>

📢 Введите текст сообщения для рассылки:

Поддерживается HTML-разметка (<b>жирный</b>, <i>курсив</i>, <code>код</code>)"""

BROADCAST_RECIPIENTS_COUNT_LINE = """📊 <b>Получателей:</b> {total_count} чел.

"""

BROADCAST_RESULT = """✅ Рассылка завершена!

📤 Отправлено: {success_count}
❌ Ошибок: {fail_count}
👥 {label}: {total_count}"""

BROADCAST_SELECT_AUDIENCE_PROMPT = """{header}<b>Выберите аудиторию для рассылки:</b>

Кому отправить сообщение?"""

BROADCAST_STARTED = """🚀 <b>Рассылка запущена!</b>

Отправляю {total_count} пользователям...

Результат придёт отдельным сообщением."""

BROADCAST_STEP_1_SELECT_AUDIENCE = "Шаг 1: Выбор аудитории"

BROADCAST_STOPPING = "⏹ Рассылка останавливается..."

BROADCAST_TEST_SENT_NOTICE = """✅ <b>Тестовое сообщение отправлено вам для проверки!</b>

"""

BROADCAST_BTN_TEST_ME = "🧪 Тест мне (Админу)"
