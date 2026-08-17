import unittest
from unittest.mock import AsyncMock, patch, MagicMock
from aiohttp.test_utils import make_mocked_request

from bot.handlers.subscription_feed import subscription_feed_handler
from database.models import User


class SubscriptionEndpointTests(unittest.IsolatedAsyncioTestCase):
    @patch("bot.handlers.subscription_feed.SubscriptionTokenService.get_user_by_token")
    @patch("bot.handlers.subscription_feed.SubscriptionFeedService.build_feed")
    @patch("bot.handlers.subscription_feed.session_scope")
    async def test_endpoint_valid_token(
        self, mock_session_scope, mock_build_feed, mock_get_user
    ):
        mock_session = AsyncMock()
        mock_session_scope.return_value.__aenter__.return_value = mock_session

        user = User(id=77, telegram_id=888, subscription_token="valid_token_xyz")
        mock_get_user.return_value = user
        mock_build_feed.return_value = (
            200,
            {
                "Content-Type": "text/plain; charset=utf-8",
                "Cache-Control": "no-store",
                "profile-title": "JUST1K VPN",
            },
            "b64_feed_payload",
        )

        req = make_mocked_request("GET", "/sub/valid_token_xyz", match_info={"token": "valid_token_xyz"})
        response = await subscription_feed_handler(req)

        self.assertEqual(response.status, 200)
        self.assertEqual(response.text, "b64_feed_payload")
        self.assertEqual(response.headers["Content-Type"], "text/plain; charset=utf-8")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        mock_get_user.assert_awaited_once_with(mock_session, "valid_token_xyz")
        mock_build_feed.assert_awaited_once_with(mock_session, user)

    @patch("bot.handlers.subscription_feed.SubscriptionTokenService.get_user_by_token")
    @patch("bot.handlers.subscription_feed.session_scope")
    async def test_endpoint_invalid_or_unknown_token(
        self, mock_session_scope, mock_get_user
    ):
        mock_session = AsyncMock()
        mock_session_scope.return_value.__aenter__.return_value = mock_session
        mock_get_user.return_value = None

        req = make_mocked_request("GET", "/sub/non_existent_token", match_info={"token": "non_existent_token"})
        response = await subscription_feed_handler(req)

        self.assertEqual(response.status, 404)
        self.assertEqual(response.text, "Not Found")

    async def test_endpoint_empty_or_excessive_token(self):
        req_empty = make_mocked_request("GET", "/sub/", match_info={"token": ""})
        resp_empty = await subscription_feed_handler(req_empty)
        self.assertEqual(resp_empty.status, 404)

        req_huge = make_mocked_request("GET", "/sub/" + "a" * 200, match_info={"token": "a" * 200})
        resp_huge = await subscription_feed_handler(req_huge)
        self.assertEqual(resp_huge.status, 404)


if __name__ == "__main__":
    unittest.main()
