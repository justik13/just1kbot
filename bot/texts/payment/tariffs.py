"""Domain texts for payment/tariffs.py."""
from __future__ import annotations

BTN_DURATION_HALF_YEAR_DISCOUNT = "⚡️ {days} дн. — {display_price} ₽ (-{discount_pct}%) 🔥"

BTN_DURATION_MONTH_DISCOUNT = "⏱ {days} дн. — {display_price} ₽ (-{discount_pct}%)"

BTN_DURATION_STANDARD = "⏱ {days} дн. — {display_price} ₽"

BTN_DURATION_YEAR_DISCOUNT = "💎 {days} дн. — {display_price} ₽ (-{discount_pct}%) 🔥"

BTN_PAYMENT_BUY_FROM_BALANCE = "💰 Купить с баланса"

BTN_PAYMENT_CHANGE_TARIFF_FROM_BALANCE = "💱 Сменить тариф с баланса"

BTN_PAYMENT_CONFIRM_PURCHASE = "✅ Подтвердить покупку"

BTN_PAYMENT_CONFIRM_TARIFF_CHANGE = "✅ Подтвердить смену тарифа"

BTN_PAYMENT_CONNECT_DEVICE = "🔌 Подключить устройство"

BTN_PAYMENT_GO_TO_RENEW = "🔄 Перейти к продлению"

BTN_PAYMENT_RENEW_SUBSCRIPTION = "🔄 Продлить подписку"

BTN_PAYMENT_RETURN_TO_PURCHASE = "Вернуться к покупке"

BTN_PAYMENT_SPECIFY_OTHER_AMOUNT = "Указать другую сумму"

BTN_PAYMENT_START_ONBOARDING = "🚀 Начать"

BTN_PAYMENT_TOPUP_PRESET_AMOUNT = "Пополнить на {amount_rub} ₽"

BTN_PAYMENT_TO_BALANCE = "💰 К балансу"

BTN_PAYMENT_TO_SUBSCRIPTION = "⏳ К подписке"

BTN_PAYMENT_TO_TARIFF_CHOICE = "← К выбору тарифа"

BTN_TARIFF_SHOWCASE_ITEM = "{group_name} — от {min_price} ₽"

PAYMENT = " 🔽"

PAYMENT_ACTIVE_CHECKOUT_EXISTS = "У вас уже есть незавершённая оплата. Завершите её или отмените перед сменой тарифа."

PAYMENT_CHANGE_TARIFF_IN_PROGRESS_NOTICE = "Сначала завершите или отмените смену тарифа."

PAYMENT_CHANGE_TARIFF_TEMPORARILY_UNAVAILABLE = "Безопасная смена тарифа временно недоступна. Продление текущего тарифа работает в обычном режиме."

PAYMENT_COMMON = "Не удалось надёжно определить текущий тариф. Покупка временно недоступна — обратитесь в поддержку для проверки подписки."

PAYMENT_CURRENT_TARIFF_UNKNOWN = """⚠️ Смена тарифа возможна только при действующей подписке.

Перейдите в раздел «Купить подписку» для оформления нового тарифа."""

PAYMENT_DURATION_HEADER = """⏱ <b>На какой срок открываем доступ?</b>
"""

PAYMENT_HUB_HEADER = """⏳ <b>Ваша подписка</b>

🟢 <b>Статус:</b> Активна
📅 <b>Действует до:</b> {valid_until} <i>(осталось {days_left})</i>

💎 <b>Тариф:</b> {tariff_name}
🔌 <b>Устройства:</b> {devices_count} / {device_limit}"""

PAYMENT_NO_TARIFFS = """💳 В данный момент нет доступных тарифов.

Обратитесь в поддержку для оформления подписки вручную."""

PAYMENT_PRICE_RUB_FORMAT = "{amount_rub} ₽"

PAYMENT_PURCHASE = """

Не хватает {shortage} ₽. Минимальное пополнение — {minimum} ₽; после покупки останется {remainder} ₽."""

PAYMENT_PURCHASE_CONFIRMATION_CARD = """✅ <b>Подтверждение: {operation_label}</b>

Тариф: <b>{tariff_name}</b>
Срок: <b>{duration_days} дней</b>
Лимит устройств: <b>{device_limit}</b>
Цена: <b>{price} ₽</b>

Баланс до покупки: <b>{balance_before} ₽</b>
Баланс после покупки: <b>{balance_after} ₽</b>"""

PAYMENT_PURCHASE_EXPIRED_RETRY = "Операция устарела. Выберите тариф заново."

PAYMENT_PURCHASE_INVALID_OPERATION = "Некорректная покупка"

PAYMENT_PURCHASE_OPEN_FAILED = "Не удалось открыть покупку."

PAYMENT_PURCHASE_OPERATION_RENEW_TITLE = "Продление"

PAYMENT_PURCHASE_PRICE_CHANGED_NOTICE = "Цена тарифа изменилась. Проверьте новую цену."

PAYMENT_PURCHASE_PRICE_EXPIRED_RETRY = "Цена изменилась. Выберите тариф ещё раз."

PAYMENT_PURCHASE_PRICE_STALE = "Цена устарела. Выберите тариф ещё раз."

PAYMENT_PURCHASE_PROCESSING_NOTICE = "Проводим покупку…"

PAYMENT_PURCHASE_RENEW_COMPLETED = "Продление выполнено"

PAYMENT_PURCHASE_STATE_CHANGED_RETRY = "Состояние подписки изменилось. Начните операцию заново."

PAYMENT_PURCHASE_SUCCESS_CARD = """🎉 <b>{operation_title}</b>

Тариф: <b>{tariff_name}</b>
Срок: {duration_days} дней
Списано: <b>{charged} ₽</b>
💰 Баланс: <b>{real_balance} ₽</b>
🎁 Бонусный баланс: <b>{bonus_balance} ₽</b>"""

PAYMENT_QUICK_RENEW_HEADER = """🔄 <b>Продление доступа</b>

Ваш текущий тариф: <b>{tariff_name}</b>
Активен до: <i>{valid_until}</i>

Выберите, на сколько продлить:"""

PAYMENT_SHORTAGE_WARNING = """
⚠️ Не хватает: <b>{amount_rub} ₽</b>"""

PAYMENT_SHOWCASE = "У вас уже подключён этот тариф. Для добавления дней используйте продление."

PAYMENT_SHOWCASE_CALC_FAILED = "Не удалось надёжно рассчитать остаток подписки. Обратитесь в поддержку."

PAYMENT_SHOWCASE_HEADER = """🛡 <b>Выберите формат подписки</b>

Выберите тариф, который подходит под ваши задачи.
"""

PAYMENT_SHOWCASE_ORDER_CARD = """💳 <b>Оформление заказа</b>

📦 Тариф: <b>{tariff_label}</b>
⏱ Срок: {days} дней
🔌 Устройства: до {device_limit}
💰 Цена: <b>{price} ₽</b>

Баланс: <b>{balance_before} ₽</b>
После покупки: <b>{balance_after} ₽</b>{shortage_line}

Покупка выполняется только после отдельного подтверждения."""

PAYMENT_SHOWCASE_PREPARE_CHANGE_FAILED = "Не удалось подготовить смену тарифа. Попробуйте ещё раз."

PAYMENT_SHOWCASE_PREPARE_FAILED = "Не удалось подготовить покупку. Попробуйте ещё раз."

PAYMENT_SHOWCASE_USE_CHANGE_SECTION = "Для этого варианта используйте раздел «Сменить тариф»."

PAYMENT_STATUS_ACTIVE_BADGE = " ✅"

PAYMENT_STATUS_UPGRADE_BADGE = " 🔼"

PAYMENT_SUBSCRIPTION_INACTIVE = """⚠️ Смена тарифа с перерасчётом остатка возможна только при действующей подписке.

Ваша подписка неактивна. Перейдите в раздел «Купить подписку» для оформления нового тарифа."""

PAYMENT_TARIFFS_SERVICES_WORKERS_PAYMENTS = "🧪"

PAYMENT_TARIFF_CHANGE_HEADER_CARD = """💱 <b>Смена тарифа</b>

Новый тариф: <b>{tariff_name}</b>
Лимит устройств: <b>{device_limit}</b>
Срок после конвертации: <b>{duration_days}</b>
Доплата: <b>{due} ₽</b>

Баланс: <b>{balance_before} ₽</b>
После смены: <b>{balance_after} ₽</b>{shortage_line}

Остаточная стоимость подписки используется только в этом расчёте и не зачисляется на свободный баланс."""

PAYMENT_USER_NOT_REGISTERED = """⚠️ <b>Профиль не найден</b>

Похоже, вы ещё не зарегистрированы в боте.

Нажмите кнопку ниже, чтобы начать."""

PURCHASE_COMPLETED = "Тариф куплен"

WORD_PURCHASE = "Покупка"


TARIFF_DISPLAY_BASIC = "📱 Базовый"
TARIFF_DISPLAY_FAMILY = "👨‍👩‍👧‍👦 Семейный"
TARIFF_DISPLAY_PRO = "🚀 Pro"
TARIFF_DISPLAY_BASIC_GROUP = "📱 Базовый (2 устр.)"
TARIFF_DISPLAY_FAMILY_GROUP = "👨‍👩‍👧‍👦 Семейный (5 устр.)"
TARIFF_DISPLAY_PRO_GROUP = "🚀 Pro ({limit} устр.)"

PAYMENT_OP_TITLE_PURCHASE = '🛒 Покупка тарифа'
PAYMENT_OP_TITLE_RENEW = '⏳ Продление тарифа'
PAYMENT_OP_TITLE_CHANGE = '🔄 Смена тарифа'
PAYMENT_OP_TITLE_DEFAULT = '🛒 Покупка'

