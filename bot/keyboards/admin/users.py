from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot import texts
from utils.tariff_names import get_tariff_group_name
from utils.text_limits import truncate_button_text


def get_admin_user_card_keyboard(
    user_id: int,
    is_banned: bool,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.button(
        text=texts.UI_BOT_KEYBOARDS_ADMIN_USERS_L15_1,
        callback_data=f"admin_subscription:{user_id}",
    )

    builder.button(
        text=texts.BTN_ADMIN_USER_BALANCE,
        callback_data=f"admin_user_balance:{user_id}",
    )

    builder.button(
        text=texts.UI_BOT_KEYBOARDS_ADMIN_USERS_L20_1,
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
            text=texts.UI_BOT_KEYBOARDS_ADMIN_USERS_L26_1,
            callback_data=f"admin_unban:{user_id}",
        )
    else:
        builder.button(
            text=texts.UI_BOT_KEYBOARDS_ADMIN_USERS_L31_1,
            callback_data=f"admin_ban:{user_id}",
        )

    builder.button(
        text=texts.UI_BOT_KEYBOARDS_ADMIN_USERS_L36_1,
        callback_data="admin_users",
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
        text=texts.UI_BOT_KEYBOARDS_ADMIN_USERS_L73_1,
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
            text=texts.UI_BOT_KEYBOARDS_ADMIN_USERS_L53_1,
            callback_data=f"admin_sub_change_tariff:{telegram_id}",
        )

        builder.button(
            text=texts.UI_BOT_KEYBOARDS_ADMIN_USERS_L58_1,
            callback_data=f"admin_sub_extend:{telegram_id}",
        )

        builder.button(
            text=texts.UI_BOT_KEYBOARDS_ADMIN_USERS_L63_1,
            callback_data=f"admin_sub_reduce:{telegram_id}",
        )
    else:
        builder.button(
            text=texts.UI_BOT_KEYBOARDS_ADMIN_USERS_L68_1,
            callback_data=f"admin_sub_grant:{telegram_id}",
        )

    builder.button(
        text=texts.UI_BOT_KEYBOARDS_ADMIN_USERS_L73_1,
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
            label += texts.RUNTIME_BOT_KEYBOARDS_ADMIN_USERS_L103_1

        builder.button(
            text=truncate_button_text(label),
            callback_data=(
                f"admin_sub_select_group:{telegram_id}:{device_limit}"
            ),
        )

    builder.button(
        text=texts.UI_BOT_KEYBOARDS_ADMIN_USERS_L112_1,
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
        text=texts.UI_BOT_KEYBOARDS_ADMIN_USERS_L138_1,
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
            text=texts.UI_BOT_KEYBOARDS_ADMIN_USERS_L155_1.format(value_0=days),
            callback_data=(
                f"admin_sub_grant_confirm:"
                f"{telegram_id}:{tariff_id}:{days}"
            ),
        )

    builder.button(
        text=texts.UI_BOT_KEYBOARDS_ADMIN_USERS_L163_1,
        callback_data=(
            f"admin_sub_grant_confirm:"
            f"{telegram_id}:{tariff_id}:36500"
        ),
    )

    builder.button(
        text=texts.UI_BOT_KEYBOARDS_ADMIN_USERS_L171_1,
        callback_data=(
            f"admin_sub_grant_custom:{telegram_id}:{tariff_id}"
        ),
    )

    builder.button(
        text=texts.UI_BOT_KEYBOARDS_ADMIN_USERS_L178_1,
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
            text=texts.UI_BOT_KEYBOARDS_ADMIN_USERS_L194_1.format(value_0=days),
            callback_data=(
                f"admin_sub_confirm_extend:{telegram_id}:{days}"
            ),
        )

    builder.button(
        text=texts.UI_BOT_KEYBOARDS_ADMIN_USERS_L201_1,
        callback_data=(
            f"admin_sub_confirm_extend:{telegram_id}:36500"
        ),
    )

    builder.button(
        text=texts.UI_BOT_KEYBOARDS_ADMIN_USERS_L208_1,
        callback_data=f"admin_sub_extend_custom:{telegram_id}",
    )

    builder.button(
        text=texts.UI_BOT_KEYBOARDS_ADMIN_USERS_L213_1,
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
        text=texts.UI_BOT_KEYBOARDS_ADMIN_USERS_L229_1,
        callback_data=confirm_callback,
    )

    builder.button(
        text=texts.UI_BOT_KEYBOARDS_ADMIN_USERS_L234_1,
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
            or texts.RUNTIME_BOT_KEYBOARDS_ADMIN_USERS_L253_1.format(value_0=profile.id)
        )

        builder.button(
            text=truncate_button_text(texts.RUNTIME_BOT_KEYBOARDS_ADMIN_USERS_L257_1.format(value_0=name)),
            callback_data=(
                f"admin_delete_device:{telegram_id}:{profile.id}"
            ),
        )

    builder.button(
        text=texts.UI_BOT_KEYBOARDS_ADMIN_USERS_L263_1,
        callback_data=f"admin_user_card:{telegram_id}",
    )

    builder.adjust(1)

    return builder.as_markup()