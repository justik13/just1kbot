import logging

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message
from sqlalchemy.exc import SQLAlchemyError

from bot import texts
from database.connection import session_scope

logger = logging.getLogger(__name__)


class DBSessionMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        try:
            async with session_scope() as session:
                data["session"] = session
                return await handler(event, data)

        except SQLAlchemyError as e:
            logger.critical(
                "Database unavailable: %s",
                type(e).__name__,
                exc_info=True,
            )

            try:
                state = data.get("state")
                if state:
                    await state.clear()
            except Exception:
                pass

            try:
                if isinstance(event, CallbackQuery):
                    await event.answer(
                        texts.ERROR_TECHNICAL_ALERT,
                        show_alert=True,
                    )
                elif isinstance(event, Message):
                    try:
                        await event.delete()
                    except Exception:
                        pass

                    from bot.keyboards.notifications import get_dismiss_notification_keyboard

                    await event.answer(
                        texts.ERROR_TECHNICAL_MESSAGE,
                        reply_markup=get_dismiss_notification_keyboard(),
                        parse_mode="HTML",
                    )
            except Exception:
                pass

            return None