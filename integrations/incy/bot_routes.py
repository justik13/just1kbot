import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, CopyTextButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from bot.keyboards import get_back_button
from config.settings import get_settings
from database.models import User
from integrations.incy.token_service import SubscriptionTokenService
from services.subscription import SubscriptionService
from utils.telegram import render_hub

router = Router()
logger = logging.getLogger(__name__)


def _build_incy_text(sub_url: str) -> str:
    return (
        texts.UI_BOT_ROUTES_PODKLYUCHENIE_CHEREZ_PRILOZHEN_23+
        texts.UI_BOT_ROUTES_VSE_VASHI_SERVERY_I_USTROYSTVA_24+
        texts.UI_BOT_ROUTES_KAK_NASTROIT_NA_TELEFONE_IOS_A_25+
        texts.UI_BOT_ROUTES_1_USTANOVITE_PRILOZHENIE_INCY__26+
        texts.UI_BOT_ROUTES_2_NAZHMITE_OTKRYT_V_INCY_DLYA__27+
        texts.UI_BOT_ROUTES_3_ESLI_PRILOZHENIE_NE_OTKRYLOS_28+
        texts.UI_BOT_ROUTES_4_VKLYUCHITE_PODKLYUCHENIE_V_P_29+
        texts.UI_BOT_ROUTES_VASHA_PERSONALNAYA_SSYLKA_30+
        f"<code>{sub_url}</code>\n\n"+
        texts.UI_BOT_ROUTES_DLYA_KOMPYUTEROV_DLYA_WINDOWS__32+
        texts.UI_BOT_ROUTES_PRI_SOZDANII_ILI_UDALENII_USTR_33
    )


def _build_incy_keyboard(sub_url: str, open_url: str):
    builder = InlineKeyboardBuilder()
    builder.button(
        text=texts.UI_BOT_ROUTES_OTKRYT_V_INCY_40,
        url=open_url,
    )
    builder.button(
        text=texts.UI_BOT_ROUTES_SKOPIROVAT_SSYLKU_44,
        copy_text=CopyTextButton(text=sub_url),
    )
    builder.button(
        text=texts.UI_BOT_ROUTES_SBROSIT_SSYLKU_48,
        callback_data="rotate_incy_token",
    )
    builder.button(
        text=texts.UI_BOT_ROUTES_NAZAD_K_USTROYSTVAM_52,
        callback_data="back_to_connections",
    )
    builder.adjust(1)
    return builder.as_markup()


@router.callback_query(F.data == "menu_incy_subscription")
async def show_incy_subscription(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
):
    await state.clear()

    if not db_user:
        await callback.answer(show_alert=False)
        await render_hub(
            callback.bot,
            callback.message.chat.id,
            texts.ERROR_USER_NOT_FOUND,
            get_back_button("back_to_connections"),
        )
        return

    if not SubscriptionTokenService.is_enabled():
        await callback.answer(texts.INCY_SUBSCRIPTION_UNAVAILABLE, show_alert=True)
        return

    has_access = await SubscriptionService.check_access(session, db_user.telegram_id)
    if not has_access:
        await callback.answer(texts.DEVICE_ACCESS_INACTIVE, show_alert=True)
        return

    await callback.answer(show_alert=False)

    settings = get_settings()
    token = await SubscriptionTokenService.get_or_create_token(session, db_user)

    sub_url = f"https://{settings.DOMAIN}/sub/{token}"
    open_url = f"https://{settings.DOMAIN}/sub/open/{token}"

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        _build_incy_text(sub_url),
        _build_incy_keyboard(sub_url, open_url),
    )


@router.callback_query(F.data == "rotate_incy_token")
async def rotate_incy_subscription(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
):
    await state.clear()

    if not db_user:
        await callback.answer(texts.ERROR_USER_NOT_FOUND, show_alert=True)
        return

    if not SubscriptionTokenService.is_enabled():
        await callback.answer(texts.INCY_SUBSCRIPTION_UNAVAILABLE, show_alert=True)
        return

    has_access = await SubscriptionService.check_access(session, db_user.telegram_id)
    if not has_access:
        await callback.answer(texts.DEVICE_ACCESS_INACTIVE, show_alert=True)
        return

    try:
        new_token = await SubscriptionTokenService.rotate_token(session, db_user)
        await session.commit()

        settings = get_settings()
        sub_url = f"https://{settings.DOMAIN}/sub/{new_token}"
        open_url = f"https://{settings.DOMAIN}/sub/open/{new_token}"

        await render_hub(
            callback.bot,
            callback.message.chat.id,
            _build_incy_text(sub_url),
            _build_incy_keyboard(sub_url, open_url),
        )
        await callback.answer(texts.UI_BOT_ROUTES_SSYLKA_USPESHNO_SBROSHENA_STAR_139, show_alert=True)
    except Exception as e:
        logger.exception("Failed to rotate subscription token for user %s: %s", db_user.id, type(e).__name__)
        await callback.answer(texts.UI_BOT_ROUTES_OSHIBKA_PRI_SBROSE_SSYLKI_POPR_142, show_alert=True)
        return
