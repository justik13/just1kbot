"""Domain texts for payment/balance.py."""
from __future__ import annotations

BALANCE_BALANCE = "💰 Баланс: <b>{int_snapshot_real_available} ₽</b>"

BALANCE_BONUS_BALANCE = "🎁 Бонусный баланс: <b>{int_snapshot_bonus_available} ₽</b>"

BALANCE_BONUS_NA_PERVOE_TOPUP = """
🎁 <b>Бонус на первое пополнение:</b>
"""

BALANCE_BONUS_REMAINING_LABEL = """
🎁 Бонусный баланс: <b>{int_balance_bonus_available} ₽</b>"""

BALANCE_ENTRY_LABELS = {'admin_adjustment': 'Корректировка', 'chargeback_debit': 'Банковский спор', 'payment_credit': 'Пополнение', 'purchase_debit': 'Покупка тарифа', 'purchase_reversal': 'Возврат покупки', 'refund_debit': 'Возврат через ЮKassa'}

BALANCE_HISTORY_EMPTY = "<i>Операций пока нет.</i>"

BALANCE_HISTORY_LIMIT_REACHED_NOTE = "Аккаунт не найден"

BALANCE_HISTORY_ROW_FORMAT = "• {value_0} · {value_1}: <b>{value_2}{value_3} ₽</b>"

BALANCE_HISTORY_SECTION_TITLE = """

<b>Последние операции</b>
"""

BALANCE_HISTORY_TITLE = """🧾 <b>История операций</b>

{history}"""

BALANCE_INSUFFICIENT_FUNDS_DIFFERENCE = "Задолженность: <b>{value_0} ₽</b>"

BALANCE_NA_BONUS_BALANCE = "• {amt} ₽ ➡️ <b>+{amt____10} ₽</b> на бонусный баланс"

BALANCE_OPERATION_DEFAULT_LABEL = "Операция"

BALANCE_OTMENENO_SSYLOK = "Отменено {count} ссылок."

BALANCE_PODROBNEE_V_MENYU_PRIGLASIT_DR = """<i>(подробнее в меню «Пригласить друга»)</i>

"""

BALANCE_RASCHET_BONUSA_K_SUMME = """💡 <b>Расчет бонуса к сумме:</b>
{bonus_lines}
"""

BALANCE_SELECT_AMOUNT_ILI_UKAZHITE_DR = "Выберите сумму или укажите другую целую сумму в рублях."

BALANCE_SIGN_MINUS = "−"

BALANCE_TOPUP_BALANCE = """➕ <b>Пополнение баланса</b>

"""

BALANCE_TOPUP_CANCELLED_NO_DEBIT = """{value_0}💰 <b>Баланс</b>

"""

BALANCE_TOPUP_CARD = """💳 <b>Пополнение баланса</b>

Сумма: <b>{value_0} ₽</b>
Текущий баланс: <b>{value_1} ₽</b>

Ссылка ведёт на защищённую страницу ЮKassa."""

BALANCE_TOPUP_CREATING_LINK_CARD = """⏳ <b>Создаём ссылку на пополнение</b>

Сумма: <b>{value_0} ₽</b>
Текущий баланс: <b>{value_1} ₽</b>

Ссылка появится здесь автоматически. Ручная проверка остаётся доступна."""

BALANCE_TOTAL_AVAILABLE_LABEL = "💰 Баланс: <b>{int_balance_real_available} ₽</b>"

BALANCE_VY_POLUCHITE_10_OT_SUMMY_POPOL = """Вы получите <b>+10%</b> от суммы пополнения на бонусный баланс!
"""

HISTORY_EMPTY = "<i>История пуста. У вас пока не было пополнений.</i>"

HISTORY_HEADER = """🧾 <b>История пополнений</b>
"""

HISTORY_LIMIT_NOTE = """
<i>Показаны последние 10 из {count} пополнений</i>"""

PAYMENT_BALANCE = "Зарезервировано: <b>{value_0} ₽</b>"
