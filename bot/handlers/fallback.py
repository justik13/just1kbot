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


@router.message()
async def handle_unknown_text(message: Message, state: FSMContext):
    await state.clear()
    try:
        await message.delete()
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