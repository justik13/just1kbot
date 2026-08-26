"""Domain copy catalogue for: admin/users.py"""

TEXTS = {
    'ADMIN_BAN_CONFIRM': 'Вы уверены, что хотите забанить пользователя <code>{telegram_id}</code>?',
    'ADMIN_BAN_FAILED': """❌ Ошибка при попытке забанить пользователя:
{message}""",
    'ADMIN_BAN_SUCCESS': """✅ Пользователь успешно забанен.
{message}""",
    'ADMIN_DELETE_DEVICE_CONFIRM': 'Удалить устройство <b>{device_name}</b> ({flag} {server_name}) у пользователя <code>{telegram_id}</code>?',
    'ADMIN_DELETE_DEVICE_ERROR': '❌ Произошла ошибка при удалении устройства.',
    'ADMIN_DELETE_DEVICE_FAILED': '❌ Не удалось удалить устройство.',
    'ADMIN_DELETE_DEVICE_SUCCESS': '✅ Устройство <b>{device_name}</b> пользователя <code>{telegram_id}</code> успешно удалено.',
    'ADMIN_MANUAL_GRANT_USER_BANNED': '❌ Невозможно выдать подписку: пользователь забанен.',
    'ADMIN_MANUAL_GRANT_USER_DELETED': '❌ Невозможно выдать подписку: пользователь удален.',
    'ADMIN_UNBAN_CONFIRM': 'Вы уверены, что хотите разбанить пользователя <code>{telegram_id}</code>?',
    'ADMIN_USER_CARD': """🛠 Админка › 👥 Пользователи › 👤 <b>Карточка</b>

<b>Telegram ID:</b> <code>{telegram_id}</code>
<b>Username:</b> @{username}
<b>Имя:</b> {first_name}
<b>Статус:</b> {status} | {ban}
💎 <b>Тариф:</b> {tariff_info}
🤝 <b>Кто пригласил:</b> {referrer_info}
<b>💰 Баланс:</b> {real_balance} ₽
<b>🎁 Бонусный баланс:</b> {bonus_balance} ₽
<b>Действует до:</b> {valid_until} ({days_left})
<b>Устройств:</b> {devices_count}/{device_limit}
<b>Приглашено рефералов:</b> {referrals_count}
<b>Регистрация:</b> {created_at}""",
    'ADMIN_USER_SEARCH_PROMPT': """🛠 Админка › 👥 Пользователи › 🔍 <b>Поиск</b>

Введите Telegram ID пользователя:""",
    'BTN_ADMIN_USER_ADD_BALANCE': '➕ Начислить баланс (Бонус)',
    'BTN_ADMIN_USER_BALANCE': '💳 Баланс пользователя',
    'BTN_ADMIN_USER_DEDUCT_BALANCE': '➖ Списать баланс',
    'BTN_ADMIN_USER_LOGS': '📜 История действий (Логи)',
    'BTN_ADMIN_USER_MESSAGE': '✉️ Написать пользователю',
    'RUNTIME_BOT_HANDLERS_ADMIN_USERS_COMMON_L55_1': '—',
    'RUNTIME_BOT_HANDLERS_ADMIN_USERS_COMMON_L64_1': 'истекла',
    'RUNTIME_BOT_HANDLERS_ADMIN_USERS_COMMON_L73_1': '{value_0} дн. {value_1} ч.',
    'RUNTIME_BOT_HANDLERS_ADMIN_USERS_COMMON_L77_1': '{value_0} ч. {value_1} мин.',
    'RUNTIME_BOT_HANDLERS_ADMIN_USERS_DEVICE_ROUTES_L135_1': '🌍',
    'RUNTIME_BOT_HANDLERS_ADMIN_USERS_DEVICE_ROUTES_L136_1': 'Неизвестно',
    'RUNTIME_BOT_KEYBOARDS_ADMIN_USERS_L103_1': ' ✅',
    'RUNTIME_BOT_KEYBOARDS_ADMIN_USERS_L253_1': 'Устройство #{value_0}',
    'RUNTIME_BOT_KEYBOARDS_ADMIN_USERS_L257_1': '🗑 {value_0}',
    'UI_BOT_HANDLERS_ADMIN_USERS_BAN_ROUTES_L143_1': 'Некорректный запрос',
    'UI_BOT_HANDLERS_ADMIN_USERS_BAN_ROUTES_L181_1': 'Некорректный запрос',
    'UI_BOT_HANDLERS_ADMIN_USERS_BAN_ROUTES_L40_1': 'Некорректный запрос',
    'UI_BOT_HANDLERS_ADMIN_USERS_BAN_ROUTES_L87_1': 'Некорректный запрос',
    'UI_BOT_HANDLERS_ADMIN_USERS_DEVICE_ROUTES_L109_1': 'Некорректный запрос',
    'UI_BOT_HANDLERS_ADMIN_USERS_DEVICE_ROUTES_L119_1': 'Некорректный запрос',
    'UI_BOT_HANDLERS_ADMIN_USERS_DEVICE_ROUTES_L180_1': 'Некорректный запрос',
    'UI_BOT_HANDLERS_ADMIN_USERS_DEVICE_ROUTES_L190_1': 'Некорректный запрос',
    'UI_BOT_HANDLERS_ADMIN_USERS_DEVICE_ROUTES_L46_1': 'Некорректный запрос',
    'UI_BOT_HANDLERS_ADMIN_USERS_LIST_ROUTES_L234_1': 'Некорректный запрос',
    'UI_BOT_KEYBOARDS_ADMIN_USERS_L112_1': '← Назад',
    'UI_BOT_KEYBOARDS_ADMIN_USERS_L138_1': '← Назад',
    'UI_BOT_KEYBOARDS_ADMIN_USERS_L155_1': '{value_0} дней',
    'UI_BOT_KEYBOARDS_ADMIN_USERS_L15_1': '📅 Подписка',
    'UI_BOT_KEYBOARDS_ADMIN_USERS_L163_1': '∞ Навсегда',
    'UI_BOT_KEYBOARDS_ADMIN_USERS_L171_1': '⌨️ Ввести вручную',
    'UI_BOT_KEYBOARDS_ADMIN_USERS_L178_1': '← Назад',
    'UI_BOT_KEYBOARDS_ADMIN_USERS_L194_1': '{value_0} дней',
    'UI_BOT_KEYBOARDS_ADMIN_USERS_L201_1': '∞ Навсегда',
    'UI_BOT_KEYBOARDS_ADMIN_USERS_L208_1': '⌨️ Ввести вручную',
    'UI_BOT_KEYBOARDS_ADMIN_USERS_L20_1': '🔧 Устройства',
    'UI_BOT_KEYBOARDS_ADMIN_USERS_L213_1': '← Назад',
    'UI_BOT_KEYBOARDS_ADMIN_USERS_L229_1': '✅ Подтвердить',
    'UI_BOT_KEYBOARDS_ADMIN_USERS_L234_1': '❌ Отмена',
    'UI_BOT_KEYBOARDS_ADMIN_USERS_L263_1': '← К карточке',
    'UI_BOT_KEYBOARDS_ADMIN_USERS_L26_1': '✅ Разбанить',
    'UI_BOT_KEYBOARDS_ADMIN_USERS_L31_1': '🚫 Забанить',
    'UI_BOT_KEYBOARDS_ADMIN_USERS_L36_1': '← К списку пользователей',
    'UI_BOT_KEYBOARDS_ADMIN_USERS_L53_1': '💎 Сменить тариф',
    'UI_BOT_KEYBOARDS_ADMIN_USERS_L58_1': '➕ Продлить доступ',
    'UI_BOT_KEYBOARDS_ADMIN_USERS_L63_1': '➖ Уменьшить дни',
    'UI_BOT_KEYBOARDS_ADMIN_USERS_L68_1': '🎫 Выдать доступ',
    'UI_BOT_KEYBOARDS_ADMIN_USERS_L73_1': '← К карточке',
}
