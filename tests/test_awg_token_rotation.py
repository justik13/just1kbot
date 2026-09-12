"""Unit tests for AWG subscription token rotation."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from database.models import User
from database.repositories import users_repo


class TestAWGTokenRotation(unittest.IsolatedAsyncioTestCase):
    async def test_rotate_subscription_token_generates_new_token(self):
        user = User(
            id=123,
            telegram_id=456,
            subscription_token="old_token_1234567890abcdef1234567890",
        )
        mock_session = AsyncMock()
        mock_session.scalar.return_value = user

        new_tok = await users_repo.rotate_subscription_token(mock_session, user)

        self.assertIsNotNone(new_tok)
        self.assertNotEqual(new_tok, "old_token_1234567890abcdef1234567890")
        self.assertEqual(user.subscription_token, new_tok)
        mock_session.flush.assert_awaited_once()

    async def test_reset_sub_routes(self):
        from bot.handlers.connection.awg_subscription_routes import (
            awg_reset_sub_execute,
            awg_reset_sub_prompt,
        )

        # 1. Prompt
        callback = MagicMock()
        callback.answer = AsyncMock()
        callback.message.chat.id = 777
        state = AsyncMock()

        with patch("bot.handlers.connection.awg_subscription_routes.render_hub", new_callable=AsyncMock) as mock_render:
            await awg_reset_sub_prompt(callback, state)
            callback.answer.assert_awaited_once()
            state.clear.assert_awaited_once()
            mock_render.assert_awaited_once()

        # 2. Execute
        callback2 = MagicMock()
        callback2.answer = AsyncMock()
        callback2.from_user.id = 777
        callback2.message.chat.id = 777
        user = User(id=1, telegram_id=777, subscription_token="token_abc")
        session = AsyncMock()
        session.commit = AsyncMock()
        session.refresh = AsyncMock()

        with (
            patch("bot.handlers.connection.awg_subscription_routes.users_repo.rotate_subscription_token", new_callable=AsyncMock) as mock_rotate,
            patch("bot.handlers.connection.awg_subscription_routes._render_manage_devices", new_callable=AsyncMock) as mock_render_dev,
        ):
            mock_rotate.return_value = "new_token_xyz"
            await awg_reset_sub_execute(callback2, state, session, db_user=user)
            mock_rotate.assert_awaited_once_with(session, user)
            session.commit.assert_awaited_once()
            callback2.answer.assert_awaited_once()
            mock_render_dev.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
