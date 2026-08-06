import asyncio
from bot import texts
from datetime import datetime

class DummyUser:
    username = "testuser"
    telegram_id = 12345

class DummyPayment:
    id = 42
    user = DummyUser()
    amount = 35.00
    currency = "RUB"
    provider_status = "succeeded"
    fulfillment_status = "succeeded"
    created_at = datetime.now()
    paid_at = datetime.now()
    external_id = "ext-123"
    manual_review_reason = ""

def safe(v):
    return str(v) if v else ""

def format_datetime(v):
    return str(v)

payment = DummyPayment()
user_label = texts.ADMIN_PAYMENT_USER_WITH_ID.format(
    username=safe(payment.user.username),
    user_id=payment.user.telegram_id,
)
display_status = "completed"
status_name = texts.PAYMENT_STATUS_NAMES.get(display_status, display_status)
status_icon = texts.PAYMENT_STATUS_ICONS.get(
    display_status,
    texts.RUNTIME_BOT_HANDLERS_ADMIN_PAYMENTS_L249_1,
)
reason_line = ""
refundable_line = ""

try:
    rendered = (
        texts.RUNTIME_BOT_HANDLERS_ADMIN_PAYMENTS_L271_1.format(
            value_0=payment.id, value_1=payment.id, value_2=user_label, 
            value_3=payment.amount, value_4=payment.currency, 
            value_5=status_icon, value_6=status_name, 
            value_7=safe(payment.provider_status), 
            value_8=safe(payment.fulfillment_status), 
            value_9=format_datetime(payment.created_at), 
            value_10=format_datetime(payment.paid_at), 
            value_11=safe(payment.external_id or texts.PLACEHOLDER_DASH), 
            value_12=refundable_line, value_13=reason_line
        )
    )
    print("SUCCESS!")
except Exception as e:
    import traceback
    traceback.print_exc()
    print(f"FAILED: {repr(e)}")
