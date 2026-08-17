import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, CopyTextButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from config.settings import get_settings
from database.models import User
from services.subscription_token_service import SubscriptionTokenService
from utils.telegram import render_hub

router = Router()
logger = logging.getLogger(__name__)


@router.callback_query(F.data == "menu_incy_subscription")
async def show_incy_subscription(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
):
    await callback.answer(show_alert=False)
    await state.clear()

    if not db_user:
        await callback.answer(texts.ERROR_USER_NOT_FOUND, show_alert=True)
        return

    settings = get_settings()
    token = await SubscriptionTokenService.get_or_create_token(session, db_user)

    sub_url = f"https://{settings.DOMAIN}/sub/{token}"
    open_url = f"https://{settings.DOMAIN}/sub/open/{token}"

    text = (
        "🔗 <b>Подключение через приложение INCY</b>\n\n"
        "Все ваши серверы и устройства в одной самообновляемой подписке.\n\n"
        "<b>📖 Как настроить:</b>\n"
        "1. Установите приложение <b>INCY</b> (App Store / Google Play).\n"
        "2. Нажмите <b>«📱 Открыть в INCY»</b> для мгновенного добавления.\n"
        "3. Если приложение не открылось, нажмите <b>«📋 Скопировать ссылку»</b> — INCY автоматически предложит импортировать её при открытии.\n"
        "4. Включите VPN в приложении.\n\n"
        "<b>Ваша персональная ссылка:</b>\n"
        f"<code>{sub_url}</code>\n\n"
        "<i>💡 При создании или удалении устройств в боте список в приложении обновится автоматически.</i>"
    )

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
        text="⬅️ Назад к устройствам",
        callback_data="back_to_connections",
    )
    builder.adjust(1)

    await render_hub(
        callback.bot,
        callback.message.chat.id,
        text,
        builder.as_markup(),
    )
