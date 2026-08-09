from aiogram import Router, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot import texts

router = Router()


@router.message(
    F.photo | F.sticker | F.voice | F.video | F.video_note
    | F.document | F.audio | F.location | F.contact | F.poll
    | F.dice | F.animation,
    StateFilter("*"),
)
async def fsm_media_guard(message: Message, state: FSMContext):
    await state.clear()
    try:
        await message.delete()
    except Exception:
        pass


async def _auto_delete_delay(bot, chat_id: int, msg_id: int, delay: float = 5.0) -> None:
    import asyncio
    await asyncio.sleep(delay)
    try:
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
async def dismiss_notification(callback: CallbackQuery):
    await callback.answer(show_alert=False)

    try:
        await callback.message.delete()
    except TelegramBadRequest:
        pass


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