"""Domain texts for payment/tariffs.py."""
from __future__ import annotations

BTN_PAYMENT_BUY_FROM_BALANCE = "💰 Купить с баланса"

BTN_PAYMENT_CHANGE_TARIFF = "⚙️ Сменить тариф"

BTN_PAYMENT_CHANGE_TARIFF_FROM_BALANCE = "💱 Сменить тариф с баланса"

BTN_PAYMENT_CONFIRM_PURCHASE = "✅ Подтвердить покупку"

BTN_PAYMENT_CONFIRM_TARIFF_CHANGE = "✅ Подтвердить смену тарифа"

BTN_PAYMENT_CONNECT_DEVICE = "🔌 Подключить устройство"

BTN_PAYMENT_CUSTOM_AMOUNT_OPTION = "Указать другую сумму"

BTN_PAYMENT_GO_TO_RENEW = "🔄 Перейти к продлению"

BTN_PAYMENT_RENEW_SUBSCRIPTION = "🔄 Продлить подписку"

BTN_PAYMENT_RETURN_TO_PURCHASE = "Вернуться к покупке"

BTN_PAYMENT_SPECIFY_OTHER_AMOUNT = "Указать другую сумму"

BTN_PAYMENT_START_ONBOARDING = "🚀 Начать"

BTN_PAYMENT_TOPUP_AMOUNT_OPTION = "Пополнить на {value_0} ₽"

BTN_PAYMENT_TOPUP_PRESET_AMOUNT = "Пополнить на {value_0} ₽"

BTN_PAYMENT_TO_BALANCE = "💰 К балансу"

BTN_PAYMENT_TO_MAIN_MENU = "🏠 В главное меню"

BTN_PAYMENT_TO_SUBSCRIPTION = "⏳ К подписке"

BTN_PAYMENT_TO_TARIFF_CHOICE = "← К выбору тарифа"

PAYMENT = " 🔽"

PAYMENT_ACTIVE_CHECKOUT_EXISTS = "⚠️ У вас уже есть не завершённая операция покупки. Завершите её или попробуйте чуть позже."

PAYMENT_CHANGE_TARIFF_TEMPORARILY_UNAVAILABLE = "Безопасная смена тарифа временно недоступна. Продление текущего тарифа продолжает работать."

PAYMENT_COMMON = "Не удалось надёжно определить текущий тариф. Покупка временно недоступна — обратитесь в поддержку для проверки подписки."

PAYMENT_CURRENT_TARIFF_UNKNOWN = """⚠️ Смена тарифа возможна только при действующей подписке.

Перейдите в раздел «Купить подписку» для оформления нового тарифа."""

PAYMENT_DASH_PLACEHOLDER = "—"

PAYMENT_DEBT_BLOCKED_NOTICE = "Покупки недоступны до погашения задолженности."

PAYMENT_DURATION_HEADER = """⏱ <b>На какой срок открываем доступ?</b>
"""

PAYMENT_HUB_HEADER = """⏳ <b>Ваша подписка</b>

🟢 <b>Статус:</b> Активна
📅 <b>Действует до:</b> {valid_until} <i>(осталось {days_left})</i>

💎 <b>Тариф:</b> {tariff_name}
🔌 <b>Устройства:</b> {devices_count} / {device_limit}

Выберите действие:"""

PAYMENT_NO_TARIFFS = """💳 В данный момент нет доступных тарифов.

Обратитесь в поддержку для оформления подписки вручную."""

PAYMENT_PRICE_RUB_FORMAT = "{value_0} ₽"

PAYMENT_PURCHASE = """

Не хватает {value_0} ₽. Минимальное пополнение — {value_1} ₽; после покупки останется {value_2} ₽."""

PAYMENT_PURCHASE_AMOUNT_PROMPT = "Введите целую сумму от <b>{value_0} ₽</b> до 5000 ₽."

PAYMENT_PURCHASE_CANCELLED_NO_DEBIT = "Покупка не выполнена. Деньги не списаны."

PAYMENT_PURCHASE_CONFIRM_CARD = """✅ <b>Подтверждение: {value_0}</b>

Тариф: <b>{value_1}</b>
Срок: <b>{value_2} дней</b>
Лимит устройств: <b>{value_3}</b>
Цена: <b>{value_4} ₽</b>

Баланс до покупки: <b>{value_5} ₽</b>
Баланс после покупки: <b>{value_6} ₽</b>"""

PAYMENT_PURCHASE_CREATING_LINK = "Создаём ссылку…"

PAYMENT_PURCHASE_DEBT_BLOCKED = "Покупки недоступны до погашения задолженности."

PAYMENT_PURCHASE_DEVICES_BLOCKED = "Сначала удалите лишние устройства."

PAYMENT_PURCHASE_DISPUTE_BLOCKED = "Покупки временно заблокированы из-за финансового спора."

PAYMENT_PURCHASE_EXPIRED_RETRY = "Операция устарела. Выберите тариф заново."

PAYMENT_PURCHASE_INSUFFICIENT_FUNDS_ALERT = "На балансе недостаточно средств."

PAYMENT_PURCHASE_INVALID_OPERATION = "Некорректная покупка"

PAYMENT_PURCHASE_NOT_ACTIVE = "Эта покупка больше не активна."

PAYMENT_PURCHASE_OPEN_FAILED = "Не удалось открыть покупку."

PAYMENT_PURCHASE_OPERATION_RENEW_TITLE = "Продление"

PAYMENT_PURCHASE_PRICE_CHANGED_NOTICE = "Цена тарифа изменилась. Проверьте новую цену."

PAYMENT_PURCHASE_PRICE_EXPIRED_RETRY = "Цена изменилась. Выберите тариф ещё раз."

PAYMENT_PURCHASE_PRICE_STALE = "Цена устарела. Выберите тариф ещё раз."

PAYMENT_PURCHASE_PROCESSING_NOTICE = "Проводим покупку…"

PAYMENT_PURCHASE_QUOTE_EXPIRED_ALERT = "Котировка устарела"

PAYMENT_PURCHASE_QUOTE_EXPIRED_RETRY = "Котировка устарела"

PAYMENT_PURCHASE_QUOTE_NOT_FOUND = "Котировка не найдена. Выберите тариф ещё раз."

PAYMENT_PURCHASE_RENEW_COMPLETED = "Продление выполнено"

PAYMENT_PURCHASE_SHORTAGE_LINE = """

Не хватает: <b>{value_0} ₽</b>."""

PAYMENT_PURCHASE_STALE_RETRY = "Операция устарела. Выберите тариф заново."

PAYMENT_PURCHASE_STATE_CHANGED_RETRY = "Состояние подписки изменилось. Начните операцию заново."

PAYMENT_PURCHASE_SUCCESS_CARD = """🎉 <b>{value_0}</b>

Тариф: <b>{value_1}</b>
Срок: {value_2} дней
Списано: <b>{value_3} ₽</b>
💰 Баланс: <b>{value_4} ₽</b>
🎁 Бонусный баланс: <b>{value_5} ₽</b>"""

PAYMENT_PURCHASE_UNAVAILABLE = "Тариф больше недоступен."

PAYMENT_QUICK_RENEW_HEADER = """🔄 <b>Продление доступа</b>

Ваш текущий тариф: <b>{tariff_name}</b>
Активен до: <i>{valid_until}</i>

Выберите, на сколько продлить:"""

PAYMENT_SHORTAGE_WARNING = """
⚠️ Не хватает: <b>{value_0} ₽</b>"""

PAYMENT_SHOWCASE = "У вас уже подключён этот тариф. Для добавления дней используйте продление."

PAYMENT_SHOWCASE_CALC_FAILED = "Не удалось надёжно рассчитать остаток подписки. Обратитесь в поддержку."

PAYMENT_SHOWCASE_CHANGE_IN_PROGRESS = "Сначала завершите или отмените смену тарифа."

PAYMENT_SHOWCASE_DEBT_BLOCKED = "Смена тарифа недоступна до погашения задолженности."

PAYMENT_SHOWCASE_DISPUTE_BLOCKED = "Смена тарифа заблокирована из-за финансового спора."

PAYMENT_SHOWCASE_DURATION_DAYS = "{value_0} дн."

PAYMENT_SHOWCASE_HEADER = """🛡 <b>Выберите формат подписки</b>

Выберите тариф, который подходит под ваши задачи.
"""

PAYMENT_SHOWCASE_OPEN_DISPUTE_BLOCKED = "Покупки временно заблокированы из-за открытого финансового спора."

PAYMENT_SHOWCASE_ORDER_CARD = """💳 <b>Оформление заказа</b>

📦 Тариф: <b>{value_0}</b>
⏱ Срок: {value_1} дней
🔌 Устройства: до {value_2}
💰 Цена: <b>{value_3} ₽</b>

Баланс: <b>{value_4} ₽</b>
После покупки: <b>{value_5} ₽</b>{value_6}

Покупка выполняется только после отдельного подтверждения."""

PAYMENT_SHOWCASE_PREPARE_CHANGE_FAILED = "Не удалось подготовить смену тарифа. Попробуйте ещё раз."

PAYMENT_SHOWCASE_PREPARE_FAILED = "Не удалось подготовить покупку. Попробуйте ещё раз."

PAYMENT_SHOWCASE_SHORTAGE_WARN = """
⚠️ Не хватает: <b>{value_0} ₽</b>"""

PAYMENT_SHOWCASE_TARIFF_CHANGE_CARD = """💱 <b>Смена тарифа</b>

Новый тариф: <b>{value_0}</b>
Лимит устройств: <b>{value_1}</b>
Срок после конвертации: <b>{value_2}</b>
Доплата: <b>{value_3} ₽</b>

Баланс: <b>{value_4} ₽</b>
После смены: <b>{value_5} ₽</b>{value_6}

Остаточная стоимость подписки используется только в этом расчёте и не зачисляется на свободный баланс."""

PAYMENT_SHOWCASE_USE_CHANGE_SECTION = "Для этого варианта используйте раздел «Сменить тариф»."

PAYMENT_STATUS_ACTIVE_BADGE = " ✅"

PAYMENT_STATUS_PENDING_ICON = "⏳"

PAYMENT_STATUS_UPGRADE_BADGE = " 🔼"

PAYMENT_SUBSCRIPTION_INACTIVE = """⚠️ Смена тарифа с перерасчётом остатка возможна только при действующей подписке.

Ваша подписка неактивна. Перейдите в раздел «Купить подписку» для оформления нового тарифа."""

PAYMENT_TARIFFS_SERVICES_WORKERS_PAYMENTS = "🧪"

PAYMENT_USER_NOT_REGISTERED = """⚠️ <b>Профиль не найден</b>

Похоже, вы ещё не зарегистрированы в боте.

Нажмите кнопку ниже, чтобы начать."""

PURCHASE_COMPLETED = "Тариф куплен"

WORD_PURCHASE = "Покупка"
