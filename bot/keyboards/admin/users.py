from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import texts
from bot.formatters import get_tariff_group_name
from utils.text_limits import truncate_button_text


def get_admin_user_card_keyboard(
    user_id: int,
    is_banned: bool,
    back_callback: str = "admin_users",
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.button(
        text=texts.ADMIN_BTN_SUBSCRIPTION,
        callback_data=f"admin_subscription:{user_id}",
    )

    builder.button(
        text=texts.BTN_ADMIN_USER_BALANCE,
        callback_data=f"admin_user_balance:{user_id}",
    )

    builder.button(
        text=texts.ADMIN_BTN_USER_DEVICES,
        callback_data=f"admin_user_devices:{user_id}",
    )

    builder.button(
        text=texts.BTN_ADMIN_USER_LOGS,
        callback_data=f"admin_user_audit:{user_id}",
    )

    builder.button(
        text=texts.BTN_ADMIN_USER_MESSAGE,
        callback_data=f"admin_send_msg:{user_id}",
    )

    if is_banned:
        builder.button(
            text=texts.ADMIN_BTN_UNBAN,
            callback_data=f"admin_unban:{user_id}",
        )
    else:
        builder.button(
            text=texts.ADMIN_BTN_BAN,
            callback_data=f"admin_ban:{user_id}",
        )

    builder.button(
        text=texts.ADMIN_BTN_BACK_TO_USERS_LIST,
        callback_data=back_callback,
    )

    builder.adjust(1)

    return builder.as_markup()


def get_admin_user_balance_keyboard(
    user_id: int,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.button(
        text=texts.BTN_ADMIN_USER_ADD_BALANCE,
        callback_data=f"admin_balance_topup:{user_id}",
    )
    builder.button(
        text=texts.BTN_ADMIN_USER_DEDUCT_BALANCE,
        callback_data=f"admin_balance_deduct:{user_id}",
    )
    builder.button(
        text=texts.ADMIN_BTN_BACK_TO_CARD,
        callback_data=f"admin_user_card:{user_id}",
    )

    builder.adjust(1)

    return builder.as_markup()



def get_admin_subscription_keyboard(
    telegram_id: int,
    has_active_sub: bool,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    if has_active_sub:
        builder.button(
            text=texts.ADMIN_BTN_CHANGE_TARIFF,
            callback_data=f"admin_sub_change_tariff:{telegram_id}",
        )

        builder.button(
            text=texts.ADMIN_BTN_EXTEND_SUBSCRIPTION,
            callback_data=f"admin_sub_extend:{telegram_id}",
        )

        builder.button(
            text=texts.ADMIN_BTN_REDUCE_SUBSCRIPTION,
            callback_data=f"admin_sub_reduce:{telegram_id}",
        )
    else:
        builder.button(
            text=texts.ADMIN_BTN_GRANT_SUBSCRIPTION,
            callback_data=f"admin_sub_grant:{telegram_id}",
        )

    builder.button(
        text=texts.ADMIN_BTN_BACK_TO_CARD,
        callback_data=f"admin_user_card:{telegram_id}",
    )

    builder.adjust(1)

    return builder.as_markup()


def get_admin_change_tariff_keyboard(
    telegram_id: int,
    groups: dict[int, list],
    current_tariff_id: int | None,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    current_device_limit = None

    if current_tariff_id:
        for device_limit, tariffs in groups.items():
            for t in tariffs:
                if t.id == current_tariff_id:
                    current_device_limit = device_limit
                    break

    for device_limit in sorted(groups.keys()):
        label = get_tariff_group_name(device_limit)

        if device_limit == current_device_limit:
            label += texts.PAYMENT_STATUS_ACTIVE_BADGE

        builder.button(
            text=truncate_button_text(label),
            callback_data=(
                f"admin_sub_select_group:{telegram_id}:{device_limit}"
            ),
        )

    builder.button(
        text=texts.BTN_BACK,
        callback_data=f"admin_subscription:{telegram_id}",
    )

    builder.adjust(1)

    return builder.as_markup()


def get_admin_grant_tariff_keyboard(
    telegram_id: int,
    groups: dict[int, list],
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    for device_limit in sorted(groups.keys()):
        label = get_tariff_group_name(device_limit)

        builder.button(
            text=truncate_button_text(label),
            callback_data=(
                f"admin_sub_grant_group:{telegram_id}:{device_limit}"
            ),
        )

    builder.button(
        text=texts.BTN_BACK,
        callback_data=f"admin_subscription:{telegram_id}",
    )

    builder.adjust(1)

    return builder.as_markup()


def get_admin_grant_days_keyboard(
    telegram_id: int,
    tariff_id: int,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    for days in (7, 30, 90):
        builder.button(
            text=texts.TIME_DAYS_FULL_FORMAT.format(days=days),
            callback_data=(
                f"admin_sub_grant_confirm:"
                f"{telegram_id}:{tariff_id}:{days}"
            ),
        )

    builder.button(
        text=texts.LABEL_FOREVER,
        callback_data=(
            f"admin_sub_grant_confirm:"
            f"{telegram_id}:{tariff_id}:36500"
        ),
    )

    builder.button(
        text=texts.ADMIN_BTN_INPUT_MANUALLY,
        callback_data=(
            f"admin_sub_grant_custom:{telegram_id}:{tariff_id}"
        ),
    )

    builder.button(
        text=texts.BTN_BACK,
        callback_data=f"admin_subscription:{telegram_id}",
    )

    builder.adjust(2, 2, 1, 1)

    return builder.as_markup()


def get_admin_extend_days_new_keyboard(
    telegram_id: int,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    for days in (7, 30, 90):
        builder.button(
            text=texts.TIME_DAYS_FULL_FORMAT.format(days=days),
            callback_data=(
                f"admin_sub_confirm_extend:{telegram_id}:{days}"
            ),
        )

    builder.button(
        text=texts.LABEL_FOREVER,
        callback_data=(
            f"admin_sub_confirm_extend:{telegram_id}:36500"
        ),
    )

    builder.button(
        text=texts.ADMIN_BTN_INPUT_MANUALLY,
        callback_data=f"admin_sub_extend_custom:{telegram_id}",
    )

    builder.button(
        text=texts.BTN_BACK,
        callback_data=f"admin_subscription:{telegram_id}",
    )

    builder.adjust(2, 2, 1, 1)

    return builder.as_markup()


def get_admin_confirm_action_keyboard(
    confirm_callback: str,
    cancel_callback: str,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.button(
        text=texts.BTN_CONFIRM,
        callback_data=confirm_callback,
    )

    builder.button(
        text=texts.BTN_CANCEL,
        callback_data=cancel_callback,
    )

    builder.adjust(2)

    return builder.as_markup()


def get_admin_user_devices_keyboard(
    telegram_id: int,
    profiles: list,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    for profile in profiles:
        name = (
            getattr(profile, "device_name", None)
            or texts.ADMIN_DEVICE_NAME_TEMPLATE.format(v0=profile.id)
        )

        builder.button(
            text=truncate_button_text(texts.ADMIN_DEVICE_DELETE_BUTTON_LABEL.format(v0=name)),
            callback_data=(
                f"admin_delete_device:{telegram_id}:{profile.id}"
            ),
        )

    builder.button(
        text=texts.ADMIN_BTN_BACK_TO_CARD,
        callback_data=f"admin_user_card:{telegram_id}",
    )

    builder.adjust(1)

    return builder.as_markup()