import logging

from bot import texts
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, CopyTextButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from database.models import User
from integrations.incy.token_service import SubscriptionTokenService
from services.subscription import SubscriptionService
from utils.telegram import render_hub

router = Router()
logger = logging.getLogger(__name__)


def _build_back_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_BACK, callback_data="back_to_connections")
    return builder.as_markup()


def _build_incy_text(sub_url: str) -> str:
    return (
        "🔗 <b>Подключение через приложение INCY [🧪 Экспериментально]</b>\n\n"
        "Все ваши серверы и устройства в одной самообновляемой подписке для мобильных устройств (<b>iOS / Android</b>).\n\n"
        "<b>📖 Как настроить на телефоне (iOS / Android):</b>\n"
        "1. Установите приложение <b>INCY</b> (App Store / Google Play).\n"
        "2. Нажмите <b>«📱 Открыть в INCY»</b> для мгновенного добавления.\n"
        "3. Если приложение не открылось, нажмите <b>«📋 Скопировать ссылку»</b> — INCY автоматически предложит импортировать её при открытии.\n"
        "4. Включите подключение в приложении.\n\n"
        "<b>Ваша персональная ссылка:</b>\n"
        f"<code>{sub_url}</code>\n\n"
        "<i>💻 Для компьютеров: для Windows 10/11 (x64) и macOS 14+ используйте <b>AmneziaVPN</b> (ключ или файл), для других версий (Windows 7/8/ARM, macOS 12/13) — <b>AmneziaWG</b> с файлом <code>.conf</code>.</i>\n\n"
        "<i>💡 При создании или удалении устройств в боте список в приложении обновится автоматически.</i>"
    )


def _build_incy_keyboard(sub_url: str, open_url: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="📱 Открыть в INCY",
        url=open_url,
    )
    builder.button(
        text="📋 Скопировать ссылку",
        copy_text=CopyTextButton(text=sub_url),
    )
    builder.button(
        text="🔄 Сбросить ссылку",
        callback_data="rotate_incy_token",
    )
    builder.button(
        text="◀️ Назад к устройствам",
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
            "❌ Пользователь не найден",
            _build_back_keyboard(),
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
        await callback.answer("✅ Ссылка успешно сброшена! Старая ссылка аннулирована.", show_alert=True)
    except Exception as e:
        logger.exception("Failed to rotate subscription token for user %s: %s", db_user.id, type(e).__name__)
        await callback.answer("Ошибка при сбросе ссылки. Попробуйте позже.", show_alert=True)
        return
