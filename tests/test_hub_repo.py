import unittest
from unittest.mock import AsyncMock, MagicMock

from database.repositories.hub_repo import (
    add_hub_message_id,
    get_hub_message_ids,
    remove_hub_message_ids,
)


class HubRepoTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_hub_message_ids(self):
        mock_session = AsyncMock()
        session.begin_nested.return_value.__aenter__.return_value = session
        session.begin_nested.return_value.__aexit__.return_value = None
        mock_res = MagicMock()
        mock_res.all.return_value = [(101,), (102,), (103,)]
        mock_session.execute.return_value = mock_res

        ids = await get_hub_message_ids(mock_session, 12345)
        self.assertEqual(ids, [101, 102, 103])
        mock_session.execute.assert_called_once()

    async def test_add_hub_message_id(self):
        mock_session = AsyncMock()
        session.begin_nested.return_value.__aenter__.return_value = session
        session.begin_nested.return_value.__aexit__.return_value = None
        await add_hub_message_id(mock_session, 12345, 101)

        mock_session.execute.assert_called_once()
        mock_session.flush.assert_called_once()

    async def test_remove_hub_message_ids_empty(self):
        mock_session = AsyncMock()
        session.begin_nested.return_value.__aenter__.return_value = session
        session.begin_nested.return_value.__aexit__.return_value = None
        await remove_hub_message_ids(mock_session, 12345, [])
        mock_session.execute.assert_not_called()
        mock_session.flush.assert_not_called()

    async def test_remove_hub_message_ids_non_empty(self):
        mock_session = AsyncMock()
        session.begin_nested.return_value.__aenter__.return_value = session
        session.begin_nested.return_value.__aexit__.return_value = None
        await remove_hub_message_ids(mock_session, 12345, [101, 102])
        mock_session.execute.assert_called_once()
        mock_session.flush.assert_called_once()


if __name__ == '__main__':
    unittest.main()
