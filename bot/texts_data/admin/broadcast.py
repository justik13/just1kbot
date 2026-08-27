"""Domain copy catalogue for: admin/broadcast.py"""

TEXTS = {
    'BROADCAST_ACTIVE_LABEL': 'Активных',
    'BROADCAST_ALREADY_RUNNING': '⏳ Рассылка уже идёт, дождитесь завершения',
    'BROADCAST_NO_RECIPIENTS': '⚠️ Нет получателей для рассылки',
    'BROADCAST_PROMPT': """🛠 Админка › 📢 <b>Рассылка</b>

📢 Введите текст сообщения для рассылки:

Поддерживается HTML-разметка (<b>жирный</b>, <i>курсив</i>, <code>код</code>)""",
    'BROADCAST_RESULT': """✅ Рассылка завершена!

📤 Отправлено: {success_count}
❌ Ошибок: {fail_count}
👥 {label}: {total_count}""",
    'BROADCAST_STARTED': """🚀 <b>Рассылка запущена!</b>

Отправляю {total_count} пользователям...

Результат придёт отдельным сообщением.""",
    'BROADCAST_STOPPING': '⏹ Рассылка останавливается...',
    'BTN_BROADCAST_ACTIVE': '🟢 Активным подпискам',
    'BTN_BROADCAST_ALL': '📢 Всем пользователям',
    'BTN_BROADCAST_EXPIRED': '🔴 Истекшим подпискам',
    'BTN_BROADCAST_EXPIRING': '⏳ Истекают < 3 дней',
    'BTN_BROADCAST_NO_SUB': '🆕 Без подписок',
    'BTN_BROADCAST_START': '🚀 Запустить рассылку ({count})',
    'BTN_BROADCAST_TEST_ADMIN': '🧪 Тест админу',
    'RUNTIME_BOT_HANDLERS_ADMIN_BROADCAST_L628_1': 'Всего',
    'UI_BOT_HANDLERS_ADMIN_BROADCAST_L116_1': '⚠️ Текст рассылки слишком длинный. Максимум {value_0} символов.',
    'UI_BOT_HANDLERS_ADMIN_BROADCAST_L125_1': '⚠️ Подпись к медиа слишком длинная. Максимум {value_0} символов.',
    'UI_BOT_HANDLERS_ADMIN_BROADCAST_L423_1': """🚨 <b>Рассылка остановлена из-за ошибки</b>
<code>{value_0}</code>""",
    'UI_BOT_HANDLERS_ADMIN_BROADCAST_L709_1': 'Рассылка не запущена',
    'UI_BOT_KEYBOARDS_ADMIN_BROADCAST_L19_1': '❌ Отмена',
    'UI_BOT_KEYBOARDS_ADMIN_BROADCAST_L32_1': '✅ Ок (Убрать)',
    'UI_BOT_KEYBOARDS_ADMIN_BROADCAST_L45_1': '✅ Прочитано (убрать)',

    'UI_BROADCAST_RASSYLKA_89': '📢 Рассылка',
    'UI_BROADCAST_SHAG_1_VYBOR_AUDITORII_89': 'Шаг 1: Выбор аудитории',
    'UI_BROADCAST_VYBERITE_AUDITORIYU_DLYA_RASSY_93': """{header}<b>Выберите аудиторию для рассылки:</b>

Кому отправить сообщение?""",
    'UI_BROADCAST_TESTOVOE_SOOBSHCHENIE_OTPRAVLE_218': """✅ <b>Тестовое сообщение отправлено вам для проверки!</b>

""",
    'UI_BROADCAST_AUDITORIYA_219': """👥 <b>Аудитория:</b> {aud_label}
""",
    'UI_BROADCAST_POLUCHATELEY_CHEL_220': """📊 <b>Получателей:</b> {total_count} чел.

""",
    'UI_BROADCAST_OZNAKOMTES_S_PREDPROSMOTROM_VY_221': 'Ознакомьтесь с предпросмотром выше и подтвердите запуск рассылки.',
    'UI_BROADCAST_TEST_MNE_ADMINU_749': '🧪 Тест мне (Админу)',
}
