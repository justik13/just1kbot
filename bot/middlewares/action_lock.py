import logging
from datetime import datetime, timezone

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery

from bot import texts
from utils.user_locks import get_user_action_lock

logger = logging.getLogger(__name__)

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
    "pay_yookassa:",
    "check_payment:",
    "cancel_invoice:",

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
    "admin_manual_grant_apply:",
    "admin_payment_refund_apply:",
    "confirm_server_delete:",
    "admin_server_toggle_apply:",
    "admin_tariff_toggle_apply:",
    "admin_maintenance_toggle_apply",
    "broadcast_send_all",
    "broadcast_send_active",
)

STALE_MAX_AGE_SECONDS = 600


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

    if message is None or message.date is None:
        return True

    now = datetime.now(timezone.utc)
    age = (now - message.date).total_seconds()

    return age > STALE_MAX_AGE_SECONDS


class ActionLockMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        if not isinstance(event, CallbackQuery):
            return await handler(event, data)

        user_id = event.from_user.id if event.from_user else None

        if not user_id:
            return await handler(event, data)

        callback_data = event.data or ""

        # Защита от старых confirm/apply кнопок.
        if _is_stale_action(callback_data) and _is_stale_callback(event):
            try:
                await event.answer(
                    "Сессия подтверждения истекла",
                    show_alert=True,
                )
            except Exception:
                pass

            return None

        if not _is_locked_action(callback_data):
            try:
                return await handler(event, data)
            except (ValueError, IndexError, TypeError):
                logger.warning(
                    "Invalid callback data or parse error: user=%s, data=%s",
                    user_id,
                    callback_data[:80],
                )

                try:
                    await event.answer(
                        "Некорректный запрос",
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
            except (ValueError, IndexError, TypeError):
                logger.warning(
                    "Invalid callback data or parse error: user=%s, data=%s",
                    user_id,
                    callback_data[:80],
                )

                try:
                    await event.answer(
                        "Некорректный запрос",
                        show_alert=True,
                    )
                except Exception:
                    pass

                return None