import logging
import re
from datetime import datetime, timezone

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery

from bot import texts
from utils.user_locks import get_user_action_lock

logger = logging.getLogger(__name__)

# Regex patterns for validating callback data parameters
CALLBACK_PARAM_PATTERNS = {
    # Device IDs: positive integers
    r"device_id=(\d+)": r"^\d+$",
    r"devices/(\d+)": r"^\d+$",
    r":(\d+):": r"^\d+$",
    r":(\d+)$": r"^\d+$",
    # Server IDs: positive integers
    r"server:(\d+)": r"^\d+$",
    r"servers/(\d+)": r"^\d+$",
    # Tariff IDs: positive integers
    r"tariff:(\d+)": r"^\d+$",
    r"tariffs/(\d+)": r"^\d+$",
    # User IDs: positive integers
    r"user:(\d+)": r"^\d+$",
    r"users/(\d+)": r"^\d+$",
    # Payment amounts: decimal numbers
    r"amount:(\d+(?:\.\d+)?)": r"^\d+(?:\.\d+)?$",
}

LOCKED_ACTION_PREFIXES = (
    # Создание устройства пользователем.
    "add_device",
    "select_server:",
    "confirm_delete_device:",
    # Админское удаление устройства.
    "admin_delete_device_apply:",
    # Генерация конфигураций.
    "download_conf:",
    "show_config:",
    # Платежи.
    "balance_create:",
    "balance_check:",
    "balance_cancel:",
    "balance_purchase_confirm:",
    "balance_change_confirm:",
    "bal_short_exact:",
    "bal_chg_short_exact:",
    # Админские платежи.
    "admin_payment_refund_apply:",
    # Админские действия с подпиской.
    "admin_sub_apply_tariff:",
    "admin_sub_apply_extend:",
    "admin_sub_apply_reduce:",
    "admin_sub_grant_apply:",
    # Админские действия с пользователями.
    "admin_ban_apply:",
    "admin_unban_apply:",
    "admin_manual_grant:",
    "admin_manual_grant_apply:",
    # Админские действия с серверами.
    "confirm_server_delete:",
    "admin_server_toggle_apply:",
    # Админские действия с тарифами.
    "admin_tariff_toggle_apply:",
    # Режим технических работ.
    "admin_maintenance_toggle_apply",
    # Рассылка.
    "broadcast_send_all",
    "broadcast_send_active",
)

STALE_ACTION_PREFIXES = (
    "confirm_delete_device:",
    "admin_delete_device_apply:",
    "admin_sub_apply_tariff:",
    "admin_sub_apply_extend:",
    "admin_sub_apply_reduce:",
    "admin_sub_grant_apply:",
    "admin_ban_apply:",
    "admin_unban_apply:",
    "admin_payment_refund_apply:",
    "admin_manual_grant_apply:",
    "confirm_server_delete:",
    "admin_server_toggle_apply:",
    "admin_tariff_toggle_apply:",
    "admin_maintenance_toggle_apply",
    "broadcast_send_all",
    "broadcast_send_active",
)

STALE_MAX_AGE_SECONDS = 600


def _validate_callback_params(callback_data: str) -> bool:
    """
    Validate callback data parameters to prevent injection attacks.
    Returns True if valid, False if suspicious patterns detected.
    """
    if not callback_data:
        return False

    # Check for SQL injection patterns
    dangerous_patterns = [
        r";\s*DROP\s+",
        r";\s*DELETE\s+",
        r";\s*UPDATE\s+",
        r";\s*INSERT\s+",
        r"--",
        r"/\*",
        r"\*/",
        r"\bOR\b\s+\d+\s*=\s*\d+",
        r"\bAND\b\s+\d+\s*=\s*\d+",
        r"\bUNION\b",
        r"\bSELECT\b",
    ]

    for pattern in dangerous_patterns:
        if re.search(pattern, callback_data, re.IGNORECASE):
            logger.warning(
                "Potential SQL injection in callback data: %s", callback_data[:100]
            )
            return False

    # Check for command injection patterns
    if any(char in callback_data for char in ["|", "`", "$", "&", ";", "<", ">"]):
        logger.warning(
            "Potential command injection in callback data: %s", callback_data[:100]
        )
        return False

    # Validate numeric parameters match expected patterns
    for param_prefix in ("device_id=", "devices/", "server:", "servers/", "tariff:", "tariffs/", "user:", "users/"):
        if param_prefix in callback_data:
            param_val = callback_data.split(param_prefix, 1)[1].split(":")[0].split("_")[0].split("/")[0]
            if not param_val.isdigit():
                logger.warning(
                    "Invalid callback parameter value: %s in %s",
                    param_val,
                    callback_data[:100],
                )
                return False

    return True


def _is_locked_action(callback_data: str) -> bool:
    if not callback_data:
        return False

    for prefix in LOCKED_ACTION_PREFIXES:
        if callback_data.startswith(prefix) or callback_data == prefix:
            return True

    return False


def _is_stale_action(callback_data: str) -> bool:
    if not callback_data:
        return False

    for prefix in STALE_ACTION_PREFIXES:
        if callback_data.startswith(prefix) or callback_data == prefix:
            return True

    return False


def _is_stale_callback(callback: CallbackQuery) -> bool:
    message = callback.message

    if message is None:
        return False

    date = getattr(message, "edit_date", None) or getattr(message, "date", None)
    if date is None:
        return False

    if isinstance(date, (int, float)):
        date = datetime.fromtimestamp(date, tz=timezone.utc)
    elif date.tzinfo is None:
        date = date.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    age = (now - date).total_seconds()

    return age > STALE_MAX_AGE_SECONDS


class ActionLockMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        if not isinstance(event, CallbackQuery):
            return await handler(event, data)

        user_id = event.from_user.id if event.from_user else None

        if not user_id:
            return await handler(event, data)

        callback_data = event.data or ""

        # Validate callback data for injection attacks
        if not _validate_callback_params(callback_data):
            try:
                await event.answer(
                    texts.UI_BOT_MIDDLEWARES_ACTION_LOCK_L199_1,
                    show_alert=True,
                )
            except Exception:
                pass

            logger.warning(
                "Invalid callback data rejected for user %d: %s",
                user_id,
                callback_data[:100],
            )

            return None

        # Защита от старых confirm/apply кнопок.
        if _is_stale_action(callback_data) and _is_stale_callback(event):
            try:
                await event.answer(
                    texts.UI_BOT_MIDDLEWARES_ACTION_LOCK_L217_1,
                    show_alert=True,
                )
            except Exception:
                pass

            return None

        if not _is_locked_action(callback_data):
            try:
                return await handler(event, data)
            except (ValueError, IndexError, TypeError) as exc:
                logger.warning(
                    "Invalid callback data or parse error: user=%s, data=%s: %s",
                    user_id,
                    callback_data[:80],
                    exc,
                    exc_info=True,
                )

                try:
                    await event.answer(
                        texts.UI_BOT_MIDDLEWARES_ACTION_LOCK_L237_1,
                        show_alert=True,
                    )
                except Exception:
                    pass

                return None

        lock = get_user_action_lock(user_id)

        if lock.locked():
            try:
                await event.answer(
                    texts.ERROR_ACTION_IN_PROGRESS,
                    show_alert=False,
                )
            except Exception:
                pass

            logger.debug(
                "Action blocked for user %d: %s (lock busy)",
                user_id,
                callback_data[:50],
            )

            return None

        async with lock:
            try:
                return await handler(event, data)
            except (ValueError, IndexError, TypeError) as exc:
                logger.warning(
                    "Invalid callback data or parse error: user=%s, data=%s: %s",
                    user_id,
                    callback_data[:80],
                    exc,
                    exc_info=True,
                )

                try:
                    await event.answer(
                        texts.UI_BOT_MIDDLEWARES_ACTION_LOCK_L276_1,
                        show_alert=True,
                    )
                except Exception:
                    pass

                return None
