"""Regression: referral leaderboard must not duplicate the count.

Bug from screenshot: '4 4 пользователя' and '(2 2 пользователя)'.
Root cause: handler used ``format_plural(count)`` (which already returns
'4 пользователя') for the ``{noun}`` slot while the template also
substitutes ``{count}`` -> '<b>4</b> 4 пользователя'.

Templates (bot/texts/user/referral.py):
  REFERRAL_LEADERBOARD_ITEM = "{pos}. {medal}{user} — <b>{count}</b> {noun}"
  REFERRAL_LEADERBOARD_YOUR_RANK = "... Ваше место: <b>#{rank}</b> ({count} {noun})"

expect ``noun`` to be a bare noun via ``pluralize()``, not ``format_plural()``.
"""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from bot import texts


class ReferralLeaderboardTemplateContractTests(unittest.TestCase):
    def test_item_template_expects_bare_noun(self):
        for count, noun in (
            (1, "пользователь"),
            (2, "пользователя"),
            (4, "пользователя"),
            (5, "пользователей"),
        ):
            rendered = texts.REFERRAL_LEADERBOARD_ITEM.format(
                pos=1, medal="", user="U", count=count, noun=noun
            )
            self.assertIn(f"<b>{count}</b> {noun}", rendered)

    def test_your_rank_template_expects_bare_noun(self):
        rendered = texts.REFERRAL_LEADERBOARD_YOUR_RANK.format(
            rank=2, count=2, noun="пользователя"
        )
        self.assertIn("(2 пользователя)", rendered)


class ReferralLeaderboardHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_show_leaderboard_has_no_duplicated_count(self):
        from bot.handlers.referral import show_referral_leaderboard

        callback = AsyncMock()
        callback.bot = MagicMock()
        callback.message.chat.id = 123
        callback.message.message_id = 1
        state = AsyncMock()
        session = AsyncMock()
        db_user = MagicMock()
        db_user.telegram_id = 872000025

        top = [(902000017, 4), (872000025, 2), (996000020, 2)]

        with (
            patch(
                "bot.handlers.referral.get_referral_leaderboard",
                return_value=top,
            ),
            patch(
                "bot.handlers.referral.get_user_referral_rank",
                return_value=(2, 2),
            ),
            patch(
                "bot.handlers.referral.render_hub",
                new_callable=AsyncMock,
            ) as mock_render,
        ):
            await show_referral_leaderboard(callback, state, session, db_user=db_user)

        mock_render.assert_awaited_once()
        rendered = mock_render.await_args.args[2]
        self.assertIn("<b>4</b> пользователя", rendered)
        self.assertIn("<b>#2</b> (2 пользователя)", rendered)


if __name__ == "__main__":
    unittest.main()
