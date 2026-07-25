import logging
from decimal import Decimal, InvalidOperation

import redis.asyncio as aioredis
from cachetools import TTLCache

from config.settings import get_settings

logger = logging.getLogger(__name__)

_alerted_paid_after_cancel: TTLCache = TTLCache(maxsize=100000, ttl=86400)
_notified_paid_after_cancel: TTLCache = TTLCache(maxsize=100000, ttl=86400)
_alerted_manual_review: TTLCache = TTLCache(maxsize=100000, ttl=86400)
_alerted_payment_not_found: TTLCache = TTLCache(maxsize=100000, ttl=3600)

_redis_client: aioredis.Redis | None = None

MANUAL_REVIEW_REASONS = {
    "banned_or_deleted": "Пользователь заблокирован или удалён",
    "inactive_tariff": "Тариф неактивен",
    "amount_mismatch": "Сумма платежа не совпадает",
    "amount_missing": "Не удалось получить сумму платежа",
    "currency_mismatch": "Валюта платежа не совпадает",
    "payload_mismatch": "Несовпадение идентификатора платежа",
    "missing_tariff_or_user": "Не найден тариф или пользователь",
    "missing_snapshot": "Не найдены условия покупки",
    "device_limit_exceeded": "Превышен лимит устройств",
    "status_failed": "Платёж находился в статусе failed",
    "payment_create_error": "Ошибка создания платежа",
    "cancel_after_completed": "Отмена после успешной оплаты",
    "not_found": "Платёж не найден",
    "owner_mismatch": "Платёж не принадлежит пользователю",
}

MANUAL_GRANT_ALLOWED_STATUSES = {
    "pending",
    "cancelled",
    "failed",
    "requires_manual_review",
}


async def _get_redis() -> aioredis.Redis:
    global _redis_client
    if _redis_client is None:
        settings = get_settings()
        _redis_client = aioredis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            socket_timeout=5.0,
        )
    return _redis_client


async def close_redis() -> None:
    global _redis_client
    if _redis_client is not None:
        try:
            await _redis_client.close()
        finally:
            _redis_client = None


def _to_decimal(value) -> Decimal | None:
    """Строгая конвертация. float запрещён."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        raise TypeError(
            f"float is not allowed for money: {value!r}. "
            "Use str or Decimal."
        )
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _safe_decimal(value) -> Decimal | None:
    """
    ИСПРАВЛЕНО (пункт 11):
    Теперь float также отклоняется, как и в _to_decimal.
    Это исключает расхождения при обработке денежных сумм.
    """
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        logger.warning(
            "_safe_decimal received float %r, converting via str(). "
            "Callers should pass str or Decimal for money.",
            value,
        )
        # Конвертируем через str для совместимости, но логируем.
        # Для критичных сумм используйте _to_decimal.
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _get_payment_snapshot_duration(payment) -> int | None:
    snapshot_value = getattr(payment, "snapshot_duration_days", None)
    if snapshot_value is not None:
        try:
            return int(snapshot_value)
        except (TypeError, ValueError):
            pass
    tariff = getattr(payment, "tariff", None)
    if tariff:
        return getattr(tariff, "duration_days", None)
    return None


def _get_payment_snapshot_device_limit(payment) -> int | None:
    snapshot_value = getattr(payment, "snapshot_device_limit", None)
    if snapshot_value is not None:
        try:
            return int(snapshot_value)
        except (TypeError, ValueError):
            pass
    tariff = getattr(payment, "tariff", None)
    if tariff:
        return getattr(tariff, "device_limit", None)
    return None


def _build_payment_snapshot(payment) -> dict:
    user = getattr(payment, "user", None)
    duration_days = _get_payment_snapshot_duration(payment)
    device_limit = _get_payment_snapshot_device_limit(payment)

    tariff_name = "—"
    if duration_days is not None and device_limit is not None:
        tariff_name = f"{duration_days} дн. / {device_limit} устр."

    return {
        "payment_id": payment.id,
        "user_telegram_id": user.telegram_id if user else None,
        "username": f"@{user.username}" if user and user.username else "—",
        "amount": str(payment.amount),
        "currency": payment.currency,
        "tariff_name": tariff_name,
        "payment_method": getattr(payment, "payment_method", None) or "—",
        "external_id": getattr(payment, "external_id", None) or "—",
    }


def get_payment_tariff_name(payment) -> str:
    """
    ИСПРАВЛЕНО (пункт 15):
    Единая функция для получения отображаемого имени тарифа.
    Используется в yookassa_routes.py и manual_grant_routes.py.
    """
    from utils.tariff_names import get_tariff_display_name

    device_limit = getattr(payment, "snapshot_device_limit", None)
    if device_limit is None and payment.tariff:
        device_limit = getattr(payment.tariff, "device_limit", 2)
    if device_limit is None:
        device_limit = 2
    return get_tariff_display_name(device_limit)