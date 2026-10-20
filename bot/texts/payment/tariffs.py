"""Domain texts for payment/tariffs.py."""
from __future__ import annotations

BTN_DURATION_HALF_YEAR_DISCOUNT = "⚡️ {days} дн. — {display_price} ₽ (-{discount_pct}%) 🔥"

BTN_DURATION_MONTH_DISCOUNT = "⏱ {days} дн. — {display_price} ₽ (-{discount_pct}%)"

BTN_DURATION_STANDARD = "⏱ {days} дн. — {display_price} ₽"

BTN_DURATION_YEAR_DISCOUNT = "💎 {days} дн. — {display_price} ₽ (-{discount_pct}%) 🔥"

BTN_PAYMENT_BUY_FROM_BALANCE = "💰 Купить с баланса"

BTN_PAYMENT_CONFIRM_FREE_CHANGE = "✅ Сменить бесплатно"

BTN_PAYMENT_CONNECT_DEVICE = "🔌 Подключить устройство"

BTN_PAYMENT_GO_TO_RENEW = "🔄 Перейти к продлению"

BTN_PAYMENT_PAY_CARD = "💳 Оплатить картой ({price} ₽)"

BTN_PAYMENT_PAY_ORDER = "💳 Оплатить {price} ₽"

BTN_PAYMENT_RENEW_SUBSCRIPTION = "🔄 Продлить подписку"

BTN_PAYMENT_START_ONBOARDING = "🚀 Начать"

BTN_PAYMENT_TO_BALANCE = "💰 К балансу"

BTN_PAYMENT_TO_SUBSCRIPTION = "⏳ К подписке"

BTN_PAYMENT_TO_TARIFF_CHOICE = "← К выбору тарифа"

BTN_TARIFF_SHOWCASE_ITEM = "{group_name} — от {min_price} ₽"

PAYMENT = " 🔽"

PAYMENT_ARCHIVED_TARIFF_NOTICE = "Ваш текущий тариф является архивным и больше недоступен для прямого продления. Выберите один из актуальных тарифов для продления доступа:"

PAYMENT_CHANGE_TARIFF_TEMPORARILY_UNAVAILABLE = "Безопасная смена тарифа временно недоступна. Продление текущего тарифа работает в обычном режиме."

PAYMENT_COMMON = "Не удалось надёжно определить текущий тариф. Покупка временно недоступна — обратитесь в поддержку для проверки подписки."

PAYMENT_DURATION_HEADER = """⏱ <b>На какой срок открываем доступ?</b>
"""

PAYMENT_HUB_HEADER = """<p>⏳ <b>Ваша подписка</b></p>
<table>
  <tr><td>Статус:</td><td>🟢 <b>Активна</b></td></tr>
  <tr><td>Действует до:</td><td>{valid_until} <i>({days_left})</i></td></tr>
  <tr><td>Тариф:</td><td><b>{tariff_name}</b></td></tr>
  <tr><td>Устройства:</td><td><b>{devices_count} / {device_limit}</b></td></tr>
</table>
<tg-button-row align="center">
  <tg-button type="callback_data" style="success" data="payment_quick_renew">🔄 Продлить подписку</tg-button>
  <tg-button type="callback_data" style="primary" data="payment_change_tariff">⚙️ Сменить тариф</tg-button>
</tg-button-row>"""

PAYMENT_NO_TARIFFS = """💳 В данный момент нет доступных тарифов.

Обратитесь в поддержку для оформления подписки вручную."""

PAYMENT_ORDER_INVOICE_CARD = """<p>💳 <b>Оплата заказа</b></p>
<table>
  <tr><td>Тариф:</td><td><b>{tariff_name}</b></td></tr>
  <tr><td>Срок:</td><td>{duration_days} дней</td></tr>
  <tr><td>К оплате:</td><td><b>{price} ₽</b></td></tr>
</table>
<blockquote expandable>Нажмите кнопку ниже для перехода к безопасной оплате.
Подписка активируется автоматически сразу после завершения платежа.
Если возникла задержка — напишите в поддержку.</blockquote>"""

PAYMENT_ORDER_PAYMENT_CANCELLED = "Платеж был отменен."

PAYMENT_ORDER_WAITING_PAYMENT = "Оплата ещё не поступила. Если вы уже оплатили в банке, подождите пару минут перед повторной проверкой, а если возникла задержка — напишите в поддержку."

PAYMENT_CREATION_STATUS_UNKNOWN = "Не удалось подтвердить создание платежа. Если вы уже оплатили в банке — зачисление произойдёт автоматически. Если не оплачивали — попробуйте ещё раз. При задержке напишите в поддержку."

PAYMENT_PRICE_RUB_FORMAT = "{amount_rub} ₽"

PAYMENT_PURCHASE_INVALID_OPERATION = "Некорректная покупка"

PAYMENT_PURCHASE_OPEN_FAILED = "Не удалось открыть покупку."

PAYMENT_PURCHASE_PROCESSING_NOTICE = "Проводим покупку…"

PAYMENT_PURCHASE_SUCCESS_CARD = """<p>🎉 <b>{operation_title}</b></p>
<table>
  <tr><td>Тариф:</td><td><b>{tariff_name}</b></td></tr>
  <tr><td>Срок:</td><td>{duration_days} дней</td></tr>
  <tr><td>Списано:</td><td><b>{charged} ₽</b></td></tr>
  <tr><td>Баланс:</td><td>{real_balance} ₽</td></tr>
  <tr><td>Бонусный баланс:</td><td>{bonus_balance} ₽</td></tr>
</table>
<tg-button-row align="center">
  <tg-button type="callback_data" style="success" data="menu_connections">🔌 Подключить устройство</tg-button>
  <tg-button type="callback_data" style="primary" data="back_to_main_menu">🏠 В главное меню</tg-button>
</tg-button-row>"""

PAYMENT_QUICK_RENEW_HEADER = """🔄 <b>Продление доступа</b>

Ваш текущий тариф: <b>{tariff_name}</b>
Активен до: <i>{valid_until}</i>

Выберите, на сколько продлить:"""

PAYMENT_SHORTAGE_WARNING = """
⚠️ Не хватает: <b>{amount_rub} ₽</b>"""

PAYMENT_SHOWCASE = "У вас уже подключён этот тариф. Для добавления дней используйте продление."

PAYMENT_SHOWCASE_HEADER = """🛡 <b>Выберите формат подписки</b>

Выберите тариф, который подходит под ваши задачи.
"""

PAYMENT_SHOWCASE_ORDER_CARD = """<p>💳 <b>Оформление заказа</b></p>
<table>
  <tr><td>Тариф:</td><td><b>{tariff_label}</b></td></tr>
  <tr><td>Срок:</td><td>{days} дней</td></tr>
  <tr><td>Устройства:</td><td>до {device_limit}</td></tr>
  <tr><td>Цена:</td><td><b>{price} ₽</b>{discount_badge}</td></tr>
  <tr><td>Баланс:</td><td>{balance_before} ₽</td></tr>
  <tr><td>После покупки:</td><td><b>{balance_after} ₽</b>{shortage_line}</td></tr>
</table>
<blockquote expandable>Покупка выполняется только после отдельного подтверждения.</blockquote>"""

PAYMENT_STATUS_ACTIVE_BADGE = " ✅"

PAYMENT_STATUS_UPGRADE_BADGE = " 🔼"

PAYMENT_TARIFF_CHANGE_HEADER_CARD = """<p>💱 <b>Смена тарифа</b></p>
<table>
  <tr><td>Новый тариф:</td><td><b>{tariff_name}</b></td></tr>
  <tr><td>Лимит устройств:</td><td><b>{device_limit}</b></td></tr>
  <tr><td>Срок после конвертации:</td><td>{duration_days}</td></tr>
  <tr><td>Доплата:</td><td><b>{due} ₽</b></td></tr>
  <tr><td>Баланс:</td><td>{balance_before} ₽</td></tr>
  <tr><td>После смены:</td><td><b>{balance_after} ₽</b>{shortage_line}</td></tr>
</table>
<blockquote expandable>Остаточная стоимость подписки используется только в этом расчёте и не зачисляется на свободный баланс.</blockquote>"""

PAYMENT_USER_NOT_REGISTERED = """⚠️ <b>Аккаунт не найден</b>

Похоже, вы ещё не зарегистрированы в боте.

Нажмите кнопку ниже, чтобы начать."""

PURCHASE_COMPLETED = "Тариф куплен"


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

PAYMENT_SHOWCASE_TABLE_ROW_TEMPLATE = "<tr><td>{name}</td><td>до {limit}</td><td><b>от {min_price} ₽</b></td></tr>"
PAYMENT_SHOWCASE_TABLE_TEMPLATE = "<table><tr><th>Тариф</th><th>Лимит</th><th>Цена</th></tr>{rows}</table>"

