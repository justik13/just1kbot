"""Domain texts for payment/topup.py."""
from __future__ import annotations

PAYMENT_LINK_READY = """💳 <b>Ссылка на оплату готова!</b>

Сумма: <b>{amount} ₽</b>
Текущий баланс: <b>{balance} ₽</b>

Нажмите кнопку ниже, чтобы перейти к оплате."""

TOPUP_ALREADY_FINISHED_ALERT = "Пополнение уже завершено"

TOPUP_CHECKING_ALERT = "Проверяем…"

TOPUP_CONFIRMED_NOTICE = "✅ Оплата подтверждена. Зачисляем деньги на баланс."

TOPUP_CREDITED_NOTICE = "✅ Баланс пополнен."

TOPUP_CUSTOM_AMOUNT_PROMPT = """Введите сумму пополнения целым числом от {minimum} до {maximum} ₽.
Например: <code>499</code>"""

TOPUP_ERROR_BALANCE_LIMIT = "Сумма превышает допустимый лимит баланса с учётом активных ссылок."

TOPUP_ERROR_BANNED = "Пополнение недоступно для этого аккаунта."

TOPUP_ERROR_BLOCKED = "Новые пополнения временно заблокированы. Обратитесь в поддержку."

TOPUP_ERROR_MAXIMUM = "Максимальная сумма одного пополнения — {maximum} ₽."

TOPUP_ERROR_MINIMUM = "Минимальная сумма пополнения — {minimum} ₽."

TOPUP_ERROR_UNFINISHED = "У вас уже есть {limit} незавершённых пополнения. Проверьте их статус или закройте ненужную ссылку."

TOPUP_ERROR_WHOLE_RUBLES = "Введите сумму целыми рублями без копеек."

TOPUP_HIDE_NOTICE = "Ссылка закрыта и больше не показывается. Если платёж уже завершён, деньги всё равно поступят на баланс."

TOPUP_INVALID_AMOUNT = "Введите целую сумму без копеек, пробелов и знаков. Например: <code>499</code>"

TOPUP_MISSING_NOTICE = "Активная ссылка пополнения не найдена."

TOPUP_NOT_FOUND_ALERT = "Пополнение не найдено"

TOPUP_OPERATION_MINIMUM = "Для выбранной операции нужно пополнить минимум на <b>{minimum} ₽</b>."

TOPUP_PROVIDER_CANCELLED_NOTICE = "Пополнение отменено платёжным провайдером."

TOPUP_SAVED_NOTICE = "Ссылка сохранена. Вы можете вернуться к пополнению позже."
