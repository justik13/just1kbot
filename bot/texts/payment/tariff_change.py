"""Domain texts for payment/tariff_change.py."""
from __future__ import annotations

ADMIN_SUB_TARIFF_CHANGED = "✅ Тариф пользователя <code>{telegram_id}</code> изменен на <b>{tariff_name}</b> (лимит: {device_limit} устр.)."

PAYMENT_ACTIVE_CHANGE_QUOTE_EXISTS = "⚠️ У вас уже создан запрос на смену тарифа. Нажмите назад и выберите его."

PAYMENT_CHANGE_TARIFF_AMOUNT_PROMPT = "Введите целую сумму от <b>{value_0} ₽</b> до 5000 ₽."

PAYMENT_CHANGE_TARIFF_CALC_EXPIRED = "Расчёт устарел. Выберите тариф ещё раз."

PAYMENT_CHANGE_TARIFF_CALC_FAILED = "Не удалось надёжно рассчитать остаток подписки."

PAYMENT_CHANGE_TARIFF_CANCELLED_NO_DEBIT = "Смена тарифа не выполнена. Деньги не списаны."

PAYMENT_CHANGE_TARIFF_CONFIRM_CARD = """✅ <b>Подтверждение смены тарифа</b>

Новый тариф: <b>{value_0}</b>
Лимит устройств: <b>{value_1}</b>
Срок после конвертации: <b>{value_2}</b>
Доплата: <b>{value_3} ₽</b>

Баланс до операции: <b>{value_4} ₽</b>
Баланс после операции: <b>{value_5} ₽</b>"""

PAYMENT_CHANGE_TARIFF_CREATING_LINK = "Создаём ссылку…"

PAYMENT_CHANGE_TARIFF_DEBT_BLOCKED = "Смена тарифа недоступна до погашения задолженности."

PAYMENT_CHANGE_TARIFF_DEVICES_BLOCKED = "Сначала удалите лишние устройства."

PAYMENT_CHANGE_TARIFF_DISPUTE_BLOCKED = "Смена тарифа заблокирована из-за финансового спора."

PAYMENT_CHANGE_TARIFF_DURATION_DAYS = "{value_0} дн."

PAYMENT_CHANGE_TARIFF_ECONOMY_CHANGED = "Экономика подписки изменилась. Создайте новый расчёт."

PAYMENT_CHANGE_TARIFF_HEADER = """⚙️ <b>Смена тарифа</b>

Ваш текущий тариф: <b>{tariff_name}</b> (действует до {valid_until})

💡 <b>Правила смены тарифа:</b>
• Оставшаяся стоимость текущей подписки учитывается при переходе.
• 🔼 <b>При увеличении устройств:</b> новые лимиты становятся доступны сразу.
• 🔽 <b>При уменьшении устройств:</b> количество ваших активных устройств не должно превышать лимит нового тарифа (при необходимости удалите лишние устройства в разделе «Устройства»).

Выберите новый тариф ниже:"""

PAYMENT_CHANGE_TARIFF_INSUFFICIENT_FUNDS = "На балансе недостаточно средств."

PAYMENT_CHANGE_TARIFF_INVALID_OPERATION = "Некорректная операция"

PAYMENT_CHANGE_TARIFF_NOT_ACTIVE = "Эта смена тарифа больше не активна."

PAYMENT_CHANGE_TARIFF_OPEN_FAILED = "Не удалось открыть смену тарифа."

PAYMENT_CHANGE_TARIFF_PRICE_CHANGED = "Цена тарифа изменилась. Проверьте новый расчёт."

PAYMENT_CHANGE_TARIFF_PROCESSING_NOTICE = "Меняем тариф…"

PAYMENT_CHANGE_TARIFF_QUOTE_EXPIRED_ALERT = "Котировка устарела"

PAYMENT_CHANGE_TARIFF_QUOTE_EXPIRED_RETRY = "Котировка устарела"

PAYMENT_CHANGE_TARIFF_QUOTE_NOT_FOUND = "Котировка не найдена. Выберите тариф ещё раз."

PAYMENT_CHANGE_TARIFF_SHORTAGE_LINE = """

Не хватает: <b>{value_0} ₽</b>."""

PAYMENT_CHANGE_TARIFF_STATE_CHANGED = "Состояние подписки изменилось. Начните заново."

PAYMENT_CHANGE_TARIFF_SUB_CHANGED = "Подписка изменилась. Создайте новый расчёт."

PAYMENT_CHANGE_TARIFF_SUCCESS_CARD = """🎉 <b>Тариф изменён</b>

Новый тариф: <b>{value_0}</b>
Срок: {value_1}
Списано: <b>{value_2} ₽</b>
💰 Баланс: <b>{value_3} ₽</b>
🎁 Бонусный баланс: <b>{value_4} ₽</b>"""

PAYMENT_CHANGE_TARIFF_UNAVAILABLE = "Выбранный тариф больше недоступен."

PAYMENT_CHANGE_TARIFF_UNAVAILABLE_NO_SUB = """⚠️ <b>Смена тарифа недоступна</b>

Смена тарифа с перерасчётом доступна только при наличии действующей подписки.
У вас сейчас нет активной подписки. Для оформления подписки воспользуйтесь кнопкой ниже."""

PAYMENT_DOWNGRADE_BLOCKED_PROFILES = """⚠️ <b>Смена тарифа невозможна</b>

У вас подключено <b>{profiles_count}</b> устройств,
а выбранный тариф поддерживает только <b>{new_limit}</b>.

Чтобы перейти на этот тариф, сначала удалите лишние устройства
в разделе «🔌 Подключения»."""

PAYMENT_DOWNGRADE_COOLDOWN_ALERT = "Смена тарифа на меньший доступна не чаще одного раза в 24 часа. Попробуйте позже."

PAYMENT_TARIFF_CHANGE = """

Не хватает {value_0} ₽. Минимальное пополнение — {value_1} ₽; после смены останется {value_2} ₽."""
