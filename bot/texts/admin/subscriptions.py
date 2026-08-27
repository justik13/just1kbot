"""Domain texts for admin/subscriptions.py."""
from __future__ import annotations

ADMIN_SUBSCRIPTION_HEADER = """🛠 Админка › 👥 Пользователь <code>{telegram_id}</code> › 💳 <b>Подписка</b>

{status_block}"""

ADMIN_SUB_CHANGE_FAILED = "❌ Ошибка при изменении подписки."

ADMIN_SUB_CHANGE_TARIFF_HEADER = """⚙️ Смена тарифа для <code>{telegram_id}</code>
Текущий тариф: <b>{current_tariff}</b> ({devices_count} устр.)
Выберите новый тариф:"""

ADMIN_SUB_CONFIRM_EXTEND = "Продлить подписку пользователю <code>{telegram_id}</code> на <b>{days_text}</b> (текущий: <code>{current_end}</code>, новый: <code>{new_end}</code>)?"

ADMIN_SUB_CONFIRM_GRANT = "Выдать тариф <b>{tariff_name}</b> пользователю <code>{telegram_id}</code> на <b>{days_text}</b> (до <code>{new_end}</code>)?"

ADMIN_SUB_CONFIRM_REDUCE = "Сократить подписку пользователю <code>{telegram_id}</code> на <b>{days}</b> дн. (текущий: <code>{current_end}</code>, новый: <code>{new_end}</code>)?"

ADMIN_SUB_CONFIRM_TARIFF = "Сменить тариф пользователю <code>{telegram_id}</code> с <b>{old_tariff}</b> на <b>{new_tariff}</b> ({devices_count} устр.)?"

ADMIN_SUB_DOWNGRADE_BLOCKED = "❌ Нельзя понизить тариф для <code>{telegram_id}</code>: подключено {devices_count} устр., а лимит нового тарифа — {new_limit}."

ADMIN_SUB_EXTEND_FAILED = "❌ Не удалось продлить подписку."

ADMIN_SUB_EXTEND_HEADER = """⏳ Продление подписки для <code>{telegram_id}</code>
Текущий срок: <code>{valid_until}</code>"""

ADMIN_SUB_EXTEND_PROMPT = "На сколько дней продлить подписку для <code>{telegram_id}</code>?"

ADMIN_SUB_EXTEND_SUCCESS = "✅ Подписка пользователя <code>{telegram_id}</code> продлена на <b>{days_text}</b> (до <code>{new_end}</code>)."

ADMIN_SUB_GRANT_CUSTOM_PROMPT = "Введите количество дней для тарифа <b>{tariff_name}</b> (пользователь <code>{telegram_id}</code>):"

ADMIN_SUB_GRANT_DAYS_HEADER = """🎁 Выдача тарифа <b>{tariff_name}</b> для <code>{telegram_id}</code>
Выберите срок:"""

ADMIN_SUB_GRANT_FAILED = "❌ Ошибка при выдаче подписки."

ADMIN_SUB_GRANT_HEADER = """🎁 Выдача подписки для <code>{telegram_id}</code>
Выберите тариф:"""

ADMIN_SUB_GRANT_SUCCESS = "✅ Пользователю <code>{telegram_id}</code> выдан тариф <b>{tariff_name}</b> на <b>{days_text}</b> (до <code>{new_end}</code>)."

ADMIN_SUB_GROUP_NOT_FOUND = "❌ Группа тарифов не найдена."

ADMIN_SUB_MENU_DEVICE_COUNT_FORMAT = "{value_0} ({value_1} устр.)"

ADMIN_SUB_NO_SUBSCRIPTION = "У пользователя нет активной подписки."

ADMIN_SUB_PERMANENT_LABEL = "Навсегда"

ADMIN_SUB_REDUCED = "✅ Подписка пользователя <code>{telegram_id}</code> сокращена до <code>{new_end}</code>."

ADMIN_SUB_REDUCE_FAILED = "❌ Не удалось сократить подписку."

ADMIN_SUB_REDUCE_PROMPT = """✂️ Сокращение подписки для <code>{telegram_id}</code>
Текущий срок: <code>{valid_until}</code>
На сколько дней сократить?"""

ADMIN_SUB_STATUS_ACTIVE = """<b>Статус:</b> 🟢 Активна ({valid_until}, {time_left})
<b>Тариф:</b> {tariff_name}
<b>Устройств:</b> {devices_count}/{device_limit}"""

ADMIN_SUB_STATUS_INACTIVE = """<b>Статус:</b> 🔴 Истекла ({valid_until})
<b>Тариф:</b> {tariff_name}"""

ADMIN_SUB_STATUS_NONE = """<b>Статус:</b> ⚪️ Нет подписки
<b>Устройств:</b> {devices_count}"""

ADMIN_SUB_TARIFF_ALREADY_SELECTED = "❌ Этот тариф уже активен."

ERROR_INVALID_DAYS_COUNT = "Некорректное количество дней"
