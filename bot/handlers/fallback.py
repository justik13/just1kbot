from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot import texts
from database.models import User

router = Router()


@router.message(
    F.photo | F.sticker | F.voice | F.video | F.video_note
    | F.document | F.audio | F.location | F.contact | F.poll
    | F.dice | F.animation,
    StateFilter("*"),
)
async def fsm_media_guard(message: Message, state: FSMContext):
    import asyncio

    from aiogram.utils.keyboard import InlineKeyboardBuilder

    await state.clear()
    try:
        await message.delete()
    except Exception:
        pass

    builder = InlineKeyboardBuilder()
    builder.button(text="🏠 В главное меню", callback_data="back_to_main_menu")
    builder.adjust(1)

    try:
        temp_msg = await message.answer(
            "🤖 <b>Неподдерживаемый тип сообщения.</b>\n\n"
            "Пожалуйста, используйте кнопки управления или текстовые команды.\n\n"
            "⏱ <i>Сообщение удалится автоматически через 5 сек.</i>",
            reply_markup=builder.as_markup(),
            parse_mode="HTML",
        )
        asyncio.create_task(
            _auto_delete_delay(message.bot, message.chat.id, temp_msg.message_id, delay=5.0)
        )
    except Exception:
        pass


async def _auto_delete_delay(bot, chat_id: int, msg_id: int, delay: float = 5.0) -> None:
    import asyncio
    await asyncio.sleep(delay)
    try:
        from utils.telegram import _load_hub_ids_from_db
        active_ids = await _load_hub_ids_from_db(chat_id)
        if msg_id in active_ids:
            return
        await bot.delete_message(chat_id=chat_id, message_id=msg_id)
    except Exception:
        pass


@router.message()
async def handle_unknown_text(message: Message, state: FSMContext):
    import asyncio

    from aiogram.utils.keyboard import InlineKeyboardBuilder

    await state.clear()
    try:
        await message.delete()
    except Exception:
        pass

    builder = InlineKeyboardBuilder()
    builder.button(text="🏠 В главное меню", callback_data="back_to_main_menu")
    builder.adjust(1)

    try:
        temp_msg = await message.answer(
            "🤖 <b>Я не понимаю произвольный текст.</b>\n\n"
            "Пожалуйста, используйте кнопки управления ниже или команду /start.\n\n"
            "⏱ <i>Сообщение удалится автоматически через 5 сек.</i>",
            reply_markup=builder.as_markup(),
            parse_mode="HTML",
        )
        asyncio.create_task(
            _auto_delete_delay(message.bot, message.chat.id, temp_msg.message_id, delay=5.0)
        )
    except Exception:
        pass


@router.callback_query(F.data == "dismiss_notification")
async def dismiss_notification(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
):
    await callback.answer(show_alert=False)
    chat_id = callback.message.chat.id if callback.message and callback.message.chat else callback.from_user.id
    msg_id = callback.message.message_id if callback.message else None

    # Determine if message is confirmed to be a standalone message (not the hub)
    hub_ids: list[int] | None = None
    if msg_id:
        try:
            from utils.telegram import _load_hub_ids_from_db
            hub_ids = await _load_hub_ids_from_db(chat_id)
        except Exception:
            hub_ids = None

    # If hub_ids was successfully loaded (including empty list) and msg_id is not in hub_ids, delete standalone message
    if msg_id and hub_ids is not None and msg_id not in hub_ids:
        try:
            if callback.message:
                await callback.message.delete()
                return
        except (TelegramBadRequest, TelegramAPIError):
            pass
        except Exception:
            pass

    # In all other cases (it IS the hub, or hub could not be determined), safely restore main menu
    from bot.handlers.start import back_to_main_menu
    await back_to_main_menu(callback, state, db_user, session)


@router.callback_query(F.data == "ignore")
async def ignore_callback(callback: CallbackQuery):
    await callback.answer(show_alert=False)


@router.callback_query(F.data.in_({"menu_profile", "back_to_profile"}))
async def legacy_profile_callback(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User | None = None,
):
    """Compatibility for old inline keyboards from pre-hub profile versions."""
    from bot.handlers.start import back_to_main_menu
    await back_to_main_menu(callback, state, db_user, session)


@router.callback_query(F.data == "white_internet")
async def white_internet_callback(callback: CallbackQuery):
    await callback.answer("🔨 Раздел находится в разработке", show_alert=True)


@router.callback_query()
async def stale_callback_fallback(
    callback: CallbackQuery,
    state: FSMContext,
):
    await state.clear()

    try:
        await callback.answer(
            texts.FALLBACK_STALE_CALLBACK,
            show_alert=True,
        )
    except Exception:
        pass
