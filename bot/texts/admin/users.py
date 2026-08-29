"""Domain texts for admin/users.py."""
from __future__ import annotations
from bot.texts.admin.common import COMMON_ALL_USERS_LABEL
from bot.texts.admin.common import COMMON_VSE, COMMON_NOVYE_7D, COMMON_3_DAYS, COMMON_BEZ_SUBSCRIPTION, COMMON_ZABANENNYE


ADMIN_BAN_CONFIRM = "Вы уверены, что хотите забанить пользователя <code>{telegram_id}</code>?"

ADMIN_BAN_FAILED = """❌ Ошибка при попытке забанить пользователя:
{message}"""

ADMIN_BAN_SUCCESS = """✅ Пользователь успешно забанен.
{message}"""

ADMIN_BONUS_REASON_LINE_FORMAT = "Причина: <i>{safe_reason}</i>"

ADMIN_BTN_BACK_TO_CARD = "← К карточке"

ADMIN_BTN_INPUT_MANUALLY = "⌨️ Ввести вручную"

ADMIN_DELETE_DEVICE_CONFIRM = "Удалить устройство <b>{device_name}</b> ({flag} {server_name}) у пользователя <code>{telegram_id}</code>?"

ADMIN_DELETE_DEVICE_ERROR = "❌ Произошла ошибка при удалении устройства."

ADMIN_DELETE_DEVICE_FAILED = "❌ Не удалось удалить устройство."

ADMIN_DELETE_DEVICE_SUCCESS = "✅ Устройство <b>{device_name}</b> пользователя <code>{telegram_id}</code> успешно удалено."

ADMIN_DIRECT_MESSAGE_BODY_FORMAT = """📨 Сообщение от администрации:

{text_to_send_or}"""

ADMIN_DIRECT_MESSAGE_HEADER_HTML = """📨 <b>Сообщение от администрации:</b>

"""

ADMIN_DIRECT_MESSAGE_HEADER_PLAIN = """📨 Сообщение от администрации:

"""

ADMIN_MANUAL_GRANT_USER_BANNED = "❌ Невозможно выдать подписку: пользователь забанен."

ADMIN_MANUAL_GRANT_USER_DELETED = "❌ Невозможно выдать подписку: пользователь удален."

ADMIN_USER_ID_FORMAT = "ID: {telegram_id}"
ADMIN_USER_ID_NO_COLON_FORMAT = "ID {telegram_id}"
ADMIN_USER_PAREN_ID_FORMAT = " (ID: {telegram_id})"


ADMIN_MASS_BONUS_AUDIENCE_LABELS = {
    "all": COMMON_ALL_USERS_LABEL,
    "active": "Только активные подписки",
    "inactive": "Только неактивные подписки",
    "expired": "Истекшие подписки"
}

ADMIN_AUDIT_LOG_DETAILS_MASS_BONUS = "Granted +{amount} RUB bonus to {count} users (batch {batch_id}). Reason: {reason}"

ADMIN_MASS_BONUS_DEFAULT_REASON = "Массовый бонус"

ADMIN_UNBAN_CONFIRM = "Вы уверены, что хотите разбанить пользователя <code>{telegram_id}</code>?"

ADMIN_USERS_BALANCE_AMOUNT_DOLZHNA_BYT_BOLSHE_0_I_N = "⚠️ Сумма должна быть больше 0 и не превышать {MAX_BALANCE_ADJUSTMENT} ₽"

ADMIN_USERS_BALANCE_BALANCE = "Баланс"

ADMIN_USERS_BALANCE_BONUS_BALANCE = """🎁 Бонусный баланс: <b>{bonus_rub} ₽</b>

"""

ADMIN_USERS_BALANCE_CHANGE = """Изменение: <b>{change_str}</b>
"""

ADMIN_USERS_BALANCE_CONFIRM_CHANGE_BALA = """⚠️ <b>Подтверждение изменения баланса:</b>

"""

ADMIN_USERS_BALANCE_DOSTUPNO_FOR_REDUCE_BONUSN = "Доступно для списания бонусных средств: <b>{int_balance_info_bonus_available} ₽</b>"

ADMIN_USERS_BALANCE_ENTER_AMOUNT_GRANT_V_R = "Введите сумму начисления в рублях (целое число от 1 до {MAX_BALANCE_ADJUSTMENT}):"

ADMIN_USERS_BALANCE_ENTER_AMOUNT_REDUCE_BONUSN = "Введите сумму списания бонусных рублей (целое число от 1 до {MAX_BALANCE_ADJUSTMENT}):"

ADMIN_USERS_BALANCE_ENTER_TEKSTOVOE_NOTE = """Введите текстовое примечание (причину начисления) для лога аудита:
"""

ADMIN_USERS_BALANCE_ERROR_DANNYE_USTARELI = "Ошибка: данные устарели."

ADMIN_USERS_BALANCE_ERROR_PRIMENENIYA_BALANCE = "⚠️ Ошибка применения баланса."

ADMIN_USERS_BALANCE_GRANT_BONUS_BALANCE = """💰 <b>Начисление бонусного баланса</b>

"""

ADMIN_USERS_BALANCE_ILI_OTPRAVTE_DEFIS_FOR_ABSTRA = "<i>(Или отправьте <code>-</code> дефис для абстрактного описания)</i>"

ADMIN_USERS_BALANCE_KORREKTIROVKA_ADMINISTRATOROM = "Корректировка администратором"

ADMIN_USERS_BALANCE_MANAGE_BALANCE_USER = """💳 <b>Управление балансом пользователя</b>

"""

ADMIN_USERS_BALANCE_NEDOSTATOCHNO_BONUS_SREDST = "⚠️ Недостаточно бонусных средств. Доступно: {int_fresh_bonus_available} ₽"

ADMIN_USERS_BALANCE_PODTVERDIT_I_PRIMENIT = "✅ Подтвердить и применить"

ADMIN_USERS_BALANCE_REAL_BALANCE = """💰 Реальный баланс: <b>{real_rub} ₽</b>
"""

ADMIN_USERS_BALANCE_REASON = """Причина: <b>{safe_reason}</b>

"""

ADMIN_USERS_BALANCE_REASON_GRANT = """📝 <b>Причина начисления (+{amount} ₽)</b>

"""

ADMIN_USERS_BALANCE_REASON_REDUCE = """📝 <b>Причина списания (-{amount} ₽)</b>

"""

ADMIN_USERS_BALANCE_REDUCE_BONUS_FUNDS = """📉 <b>Списание бонусных средств</b>

"""

ADMIN_USERS_BALANCE_SELECT_ACTION_BELOW = "Выберите действие ниже:"

ADMIN_USERS_BALANCE_SUCCESS_BONUS_BALANCE_POLZO = """{header}✅ <b>Успешно!</b> Бонусный баланс пользователя {user_telegram_id} изменен на <b>{change_formatted}</b>.
Причина: <i>{safe_reason}</i>"""

ADMIN_USERS_BALANCE_SUCCESS_PRIVEDENO_V_ACTION = "✅ Успешно приведено в действие!"

ADMIN_USERS_BALANCE_S_VASHEGO_BONUS_BALANCE_SP = """💳 <b>С вашего бонусного баланса списано: -{amount} ₽.</b>
Причина: <i>{safe_reason}</i>"""

ADMIN_USERS_BALANCE_TYPE_ACCOUNT_BONUS_BALANCE_RUB = """Тип счета: <b>🎁 Бонусный баланс (RUB)</b>
"""

ADMIN_USERS_BALANCE_USER = """Пользователь: <b>{safe_username_str}</b>
"""

ADMIN_USERS_BALANCE_VY_UVERENY_CHTO_KHOTITE_PRIMEN = "Вы уверены, что хотите применить данное изменение?"

ADMIN_USERS_BALANCE_V_BALANCE = "💳 В баланс"

ADMIN_USERS_DEVICE_AKTIVNOST = """   Активность: <i>{last_conn}</i>
"""

ADMIN_USERS_DEVICE_DEVICE = "Устройство #{profile_id}"

ADMIN_USERS_DEVICE_DEVICES = "📱 Устройства"

ADMIN_USERS_DEVICE_DEVICES_POLZOVATELYA_ID = """📱 <b>Устройства пользователя ID {telegram_id}:</b>

"""

ADMIN_USERS_DEVICE_ID_DEVICES = """   🆔 ID устройства: <code>{profile_id}</code>
"""

ADMIN_USERS_DEVICE_NEIZVESTNYY_SERVER = "Неизвестный сервер"

ADMIN_USERS_DEVICE_NE_BYLO_PODKLYUCHENIYA = "⏱ не было подключения"

ADMIN_USERS_DEVICE_OFLAYN = "🔴 <b>Офлайн</b>"

ADMIN_USERS_DEVICE_SERVER = """   🖥 Сервер: {server_flag} <b>{server_name}</b>
"""

ADMIN_USERS_DEVICE_SOSTOYANIE = """   Состояние: {status_hs}
"""

ADMIN_USERS_DEVICE_TRAFIK = """   Трафик: <code>{traffic_total}</code>
"""

ADMIN_USERS_DEVICE_U_POLZOVATELYA_POKA_NET_SOZDAN = "<i>У пользователя пока нет созданных устройств.</i>"

ADMIN_USERS_DEVICE_V_SETI_AKTIVNOST_3_MIN = "🟢 <b>В сети (активность ≤ 3 мин)</b>"

ADMIN_USERS_LIST_ACTION = "Действие"

ADMIN_USERS_LIST_EMPTY_NOTICE = "<i>Пользователи не найдены.</i>"

ADMIN_USERS_LIST_ENTER_USERNAME_TELEGRAM_ID_I = "⚠️ Введите @username, Telegram ID или ID пользователя для поиска."

ADMIN_USERS_LIST_HISTORY_DEYSTVIY_POLZOVATELYA = "📜 <b>История действий пользователя ID {user_telegram_id}:</b>"

ADMIN_USERS_LIST_HISTORY_DEYSTVIY_PUSTA = "<i>История действий пуста.</i>"

ADMIN_USERS_LIST_K_KARTOCHKE_POLZOVATELYA = "🔙 К карточке пользователя"

ADMIN_USERS_LIST_NAZAD = "🔙 Назад"

ADMIN_USERS_LIST_NEIZVESTNYY_FILTR = "Неизвестный фильтр"

ADMIN_USERS_LIST_NEKORREKTNYY_PARAMETR_FILTRA = "Некорректный параметр фильтра"

ADMIN_USERS_LIST_SELECT_SERVER = "🖥 <b>Выберите сервер:</b>"

ADMIN_USERS_LIST_SELECT_STRANU = "🌐 <b>Выберите страну:</b>"

ADMIN_USERS_LIST_SELECT_TARIFF = "💎 <b>Выберите тариф:</b>"

ADMIN_USERS_LIST_SERVEROV_NET = "Серверов нет"

ADMIN_USERS_LIST_STRAN_NET = "Стран нет"

ADMIN_USERS_LIST_TARIFOV_NET = "Тарифов нет"

ADMIN_USERS_LIST_TOTAL_ZAPISEY = """<i>Всего записей: {total_count}</i>
"""

ADMIN_USERS_LIST_USERS_NE_NAYDENY_PO_FILT = "Пользователи не найдены по фильтру"

ADMIN_USERS_LIST_USER_PO_ZAPROSU_NE_NAYDE = "🔍 Пользователь по запросу <b>{safe_message_text}</b> не найден."

ADMIN_USERS_MASS_BONUS_ALL_POLZOVATELYAM = "👥 Всем пользователям"

ADMIN_USERS_MASS_BONUS_AMOUNT_BONUS_GRANT_N = """💰 <b>Сумма бонусного начисления на каждого пользователя:</b>

"""

ADMIN_USERS_MASS_BONUS_AUDIENCE = """• Аудитория: <b>{aud_label}</b>
"""

ADMIN_USERS_MASS_BONUS_BONUS_KAZHDOMU = """• Бонус каждому: <b>+{amount} ₽</b>
"""

ADMIN_USERS_MASS_BONUS_CONFIRM = "Подтверждение"

ADMIN_USERS_MASS_BONUS_CONFIRM_MASSOVOGO_NACHI = """⚠️ <b>Подтверждение массового начисления бонусов:</b>

"""

ADMIN_USERS_MASS_BONUS_ENTER_AMOUNT_BONUSOV_V_RUBLYA = "Введите сумму бонусов в рублях (целое число, например <code>100</code>):"

ADMIN_USERS_MASS_BONUS_ENTER_KORREKTNUYU_AMOUNT_NACH = "⚠️ Введите корректную сумму начисления от 1 до 100 000 ₽"

ADMIN_USERS_MASS_BONUS_ENTER_MESSAGE_FOR_POLZ = """Введите сообщение для пользователей и лога аудита
"""

ADMIN_USERS_MASS_BONUS_ITOGI = "Итоги"

ADMIN_USERS_MASS_BONUS_MASSOVAYA_KOMPENSATSIYA = "Массовая компенсация"

ADMIN_USERS_MASS_BONUS_MASSOVOE_GRANT_BONUS = """🎁 <b>Массовое начисление бонусного баланса</b>

"""

ADMIN_USERS_MASS_BONUS_MASSOVOE_GRANT_BONUSOV_P = """{header}⏳ <b>Массовое начисление бонусов (по +{amount} ₽) запущено в фоновом режиме!</b>

"""

ADMIN_USERS_MASS_BONUS_MASSOVOE_GRANT_BONUSOV_Z = """{header}✅ <b>Массовое начисление бонусов завершено!</b>

"""

ADMIN_USERS_MASS_BONUS_MASSOVOE_GRANT_UZHE_VYPO = "⚠️ Массовое начисление уже выполняется!"

ADMIN_USERS_MASS_BONUS_MASSOVOE_GRANT_ZAPUSHCHE = "🚀 Массовое начисление запущено в фоне!"

ADMIN_USERS_MASS_BONUS_NAPRIMER_KOMPENSATSIYA_ZA_SBOY = "(например: <i>Компенсация за сбой на серверах 09.08</i>):"

ADMIN_USERS_MASS_BONUS_NEW_MASS_BONUS = "🎁 Новый массовый бонус"

ADMIN_USERS_MASS_BONUS_OBSHCHIY_BYUDZHET_BONUSOV = """• Общий бюджет бонусов: <b>{total_budget} ₽</b>
"""

ADMIN_USERS_MASS_BONUS_OSHIBOK = """• Ошибок: <b>{fail_count}</b>
"""

ADMIN_USERS_MASS_BONUS_PO_ZAVERSHENII_OPERATIONS_VAM_P = "По завершении операции вам придет уведомление со статистикой."

ADMIN_USERS_MASS_BONUS_REASON = """• Причина: <i>{safe_reason}</i>

"""

ADMIN_USERS_MASS_BONUS_REASON_MASSOVOGO_NACHISLENIY = """📝 <b>Причина массового начисления (+{amount} ₽):</b>

"""

ADMIN_USERS_MASS_BONUS_RECIPIENTS_CHEL = """• Получателей: <b>{user_count} чел.</b>
"""

ADMIN_USERS_MASS_BONUS_REZULTAT = "Результат"

ADMIN_USERS_MASS_BONUS_SELECT_AUDIENCE = "Выбор аудитории"

ADMIN_USERS_MASS_BONUS_SELECT_TSELEVUYU_GRUPPU_POLZ = "Выберите целевую группу пользователей для получения компенсации/бонусов:"

ADMIN_USERS_MASS_BONUS_TOLKO_BEZ_SUBSCRIPTION = "⏳ Только без подписки"

ADMIN_USERS_MASS_BONUS_TOLKO_S_AKTIVNOY_PODPISKOY = "⚡ Только с активной подпиской"

ADMIN_USERS_MASS_BONUS_VVOD_PRICHINY = "Ввод причины"

ADMIN_USERS_MASS_BONUS_VVOD_SUMMY = "Ввод суммы"

ADMIN_USERS_MASS_BONUS_VY_UVERENY_CHTO_KHOTITE_ZAPUST = "Вы уверены, что хотите запустить начисление?"

ADMIN_USERS_MASS_BONUS_V_ADMIN_MENYU = "🏠 В админ-меню"

ADMIN_USERS_MASS_BONUS_ZABLOKIROVALI_BOTA = "• Заблокировали бота: <b>{blocked_count}</b>"

ADMIN_USERS_MASS_BONUS_ZACHISLENO_CHEL_KAZHDOMU = """• Зачислено: <b>{success_count} чел.</b> (+{amount} ₽ каждому)
"""

ADMIN_USERS_MASS_BONUS_ZAPUSTIT_GRANT = "🚀 Запустить начисление"

ADMIN_USERS_MESSAGE_ENTER_TEXT_MESSAGE_KO = """Введите текст сообщения, которое бот доставит пользователю от имени администрации.

"""

ADMIN_USERS_MESSAGE_ERROR_NE_NAYDEN_TSELEVOY_POL = "❌ Ошибка: не найден целевой пользователь."

ADMIN_USERS_MESSAGE_MESSAGE_OT_ADMINISTRATSII = """📨 <b>Сообщение от администрации:</b>

{text_to_send_or}"""

ADMIN_USERS_MESSAGE_MESSAGE_POLZOVATELYU_ID_U = "✅ <b>Сообщение пользователю ID {target_telegram_id} успешно отправлено!</b>"

ADMIN_USERS_MESSAGE_NE_UDALOS_OTPRAVIT_SOOBSHCHENI = "❌ <b>Не удалось отправить сообщение:</b> {safe_error_reason}"

ADMIN_USERS_MESSAGE_PODDERZHIVAETSYA_HTML_RAZMETKA = "<i>Поддерживается HTML-разметка. Для отмены нажмите кнопку ниже.</i>"

ADMIN_USERS_MESSAGE_POZHALUYSTA_OTPRAVTE_TEKSTOVOE = "⚠️ Пожалуйста, отправьте текстовое сообщение или медиа с подписью."

ADMIN_USERS_MESSAGE_SENDING_MESSAGE_POLZOVA = """✉️ <b>Отправка сообщения пользователю</b>

"""

ADMIN_USERS_MESSAGE_USER_ID = """Пользователь: <b>{safe_user_username_or_str_user_telegram_id}</b> (ID: <code>{user_telegram_id}</code>)

"""

ADMIN_USERS_MESSAGE_USER_ZABLOKIROVAL_BOTA = "Пользователь заблокировал бота"

ADMIN_USER_BALANCE_INSUFFICIENT_FOR_DEBIT = """⚠️ <b>У пользователя недостаточно бонусных средств.</b>
"""

ADMIN_USER_BAN_STATUS_LABELS = {'user_not_found': 'Пользователь не найден', 'already_banned': 'уже забанен', 'banned': 'забанен', 'already_unbanned': 'уже разбанен', 'unbanned': 'разбанен'}

ADMIN_USER_BONUS_ACCREDITED_NOTIFICATION = """🎁 <b>Вам начислен бонусный баланс: +{amount} ₽!</b>
"""

ADMIN_USER_CARD = """🛠 Админка › 👥 Пользователи › 👤 <b>Карточка</b>

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
<b>Регистрация:</b> {created_at}"""

ADMIN_DEVICE_NAME_TEMPLATE = "Устройство #{v0}"

ADMIN_DEVICE_DELETE_BUTTON_LABEL = "🗑 {v0}"

ADMIN_BTN_SUBSCRIPTION = "📅 Подписка"

ADMIN_USER_DEBIT_REASON_PROMPT = """Введите текстовое примечание (причину списания) для лога аудита:
"""

ADMIN_USER_DEVICES_HEADER = """{header}📱 <b>Устройства пользователя ID {telegram_id}:</b>
"""

ADMIN_BTN_BACK_TO_USERS_LIST = "← К списку пользователей"

ADMIN_BTN_BAN = "🚫 Забанить"

ADMIN_BTN_CHANGE_TARIFF = "💎 Сменить тариф"


ADMIN_USER_FILTER_LABELS = {'all': COMMON_VSE, 'new': COMMON_NOVYE_7D, 'new_24h': COMMON_NOVYE_7D, 'new_7d': COMMON_NOVYE_7D, 'expiring_3d': COMMON_3_DAYS, 'active': '⚡ С подпиской', 'expired': COMMON_BEZ_SUBSCRIPTION, 'no_sub': COMMON_BEZ_SUBSCRIPTION, 'banned': COMMON_ZABANENNYE, 'problem': COMMON_ZABANENNYE, 'server': 'Сервер #{filter_param}', 'tariff': 'Тариф #{filter_param}'}

ADMIN_BTN_USER_DEVICES = "🔧 Устройства"

ADMIN_BTN_UNBAN = "✅ Разбанить"

ADMIN_BTN_EXTEND_SUBSCRIPTION = "➕ Продлить доступ"

ADMIN_BTN_REDUCE_SUBSCRIPTION = "➖ Уменьшить дни"

ADMIN_BTN_GRANT_SUBSCRIPTION = "🎫 Выдать доступ"

ADMIN_USER_SEARCH_PROMPT = """🛠 Админка › 👥 Пользователи › 🔍 <b>Поиск</b>

Введите Telegram ID пользователя:"""

INVITED_BY_ID_LINE = """
🤝 Вас пригласил: ID <code>{referrer_id}</code>"""

INVITED_BY_NAMED_LINE = """
🤝 Вас пригласил: {name} (ID: <code>{referrer_id}</code>)"""

LABEL_FOREVER = "∞ Навсегда"

LABEL_NOT_SET_LINK_HIDDEN = "Не задано (ссылка скрыта от пользователей)"

STATUS_EXPIRED_LABEL = "истекла"

TIME_DAYS_FULL_FORMAT = "{days} дней"


ADMIN_USERS_DEVICE_ROW_HEADER = "  • 📱 <b>{name}</b>\n"


ADMIN_USERS_FILTER_SERVER_BUTTON = "🖥 {flag} {server_name}"
ADMIN_USERS_FILTER_COUNTRY_BUTTON = "🌐 {country}"
ADMIN_USERS_FILTER_TARIFF_BUTTON = "💎 {tariff_group}"
ADMIN_FILTER_FLAG_FALLBACK = "🌐"

ADMIN_USER_FILTER_ALL_COUNT = "{f_name} ({count})"
ADMIN_USER_FILTER_NEW_7D_COUNT = "🆕 {f_name} ({count})"
ADMIN_USER_FILTER_ACTIVE_COUNT = "🟢 {f_name} ({count})"
ADMIN_USER_FILTER_EXPIRING_COUNT = "⏳ {f_name} ({count})"
ADMIN_USER_FILTER_EXPIRED_COUNT = "🔴 {f_name} ({count})"
ADMIN_USER_FILTER_BANNED_COUNT = "🚫 {f_name} ({count})"
