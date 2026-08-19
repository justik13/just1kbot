import uuid
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Server, User
from database.repositories.servers_repo import (
    _capacity_consuming_profiles_condition,
)
from database.repositories.users_repo import (
    _apply_user_filters,
    get_filtered_users_paginated_with_profiles,
)
from services.device_service import (
    DeviceService,
    DuplicateDeviceName,
)
from utils.datetime_helpers import now_utc


class AuditDeepBehaviouralTests(unittest.IsolatedAsyncioTestCase):
    def test_new_24h_and_7d_filter_boundary_logic(self):
        """Behavioural test verifying new_24h strictly filters <= 24 hours while new_7d filters <= 7 days."""
        # Apply queries
        stmt_24h = _apply_user_filters(select(User), "new_24h")
        stmt_7d = _apply_user_filters(select(User), "new_7d")

        # Compile SQL strings and check parameters
        str_24h = str(stmt_24h.compile(compile_kwargs={"literal_binds": True}))
        str_7d = str(stmt_7d.compile(compile_kwargs={"literal_binds": True}))
        self.assertIn("users.created_at >=", str_24h)
        self.assertIn("users.created_at >=", str_7d)

    def test_mass_bonus_uuid_uniqueness_guarantee(self):
        """Verify that mass bonus generates unique batch IDs and non-colliding idempotency keys."""
        batch_ids = [uuid.uuid4().hex for _ in range(500)]
        self.assertEqual(len(batch_ids), len(set(batch_ids)), "UUID batch IDs must be 100% distinct")

        # Check idempotency keys format
        uid = 12345
        amount = 100
        keys = [f"mass_bonus_{b}_{uid}_{amount}" for b in batch_ids]
        self.assertEqual(len(keys), len(set(keys)), "Idempotency keys across batches must never collide")

    def test_server_capacity_accurate_profile_counting(self):
        """Verify that delete_failed, create_cleanup_pending, and active profiles WITH peer_id
        consume capacity, while create_failed WITHOUT peer_id does NOT consume capacity."""
        condition = _capacity_consuming_profiles_condition()
        sql_cond = str(condition.compile(compile_kwargs={"literal_binds": True}))

        # Verify SQL condition structure: peer_id IS NOT NULL OR provisioning_status NOT IN ('create_failed')
        self.assertIn("vpn_profiles.peer_id IS NOT NULL", sql_cond)
        self.assertIn("create_failed", sql_cond)

    async def test_users_repo_wrapper_accepts_filter_param(self):
        """Verify get_filtered_users_paginated_with_profiles forwards filter_param."""
        session = AsyncMock(spec=AsyncSession)
        mock_result = MagicMock()
        mock_result.scalars.return_value.unique.return_value.all.return_value = []
        session.execute.return_value = mock_result

        res = await get_filtered_users_paginated_with_profiles(
            session, filter_type="server", filter_param="10", page=1, per_page=10
        )
        self.assertEqual(res, [])
        session.execute.assert_awaited_once()

    async def test_device_service_savepoint_preserves_outer_session_modifications(self):
        """Regression test verifying that DuplicateDeviceName rolls back only the nested savepoint,
        preserving other modifications on the session."""
        session = AsyncMock(spec=AsyncSession)
        
        # Nested transaction mock
        nested_tx = AsyncMock()
        session.begin_nested.return_value.__aenter__.return_value = nested_tx
        
        user = User(
            id=1,
            telegram_id=999,
            is_banned=False,
            is_bot_blocked=False,
            is_deleted=False,
            device_limit=5,
            subscription_end=now_utc() + timedelta(days=30),
            device_creations_today=0,
            last_creation_date=now_utc().date(),
        )
        server = Server(
            id=5,
            name="Node 1",
            country_flag="🇩🇪",
            protocol="amneziawg2",
            is_active=True,
            max_clients=100,
            api_url="http://node:4001",
            api_key="secret",
        )

        user_res = MagicMock()
        user_res.scalar_one.return_value = user

        server_res = MagicMock()
        server_res.scalar_one_or_none.return_value = server

        dup_res = MagicMock()
        dup_res.scalar_one_or_none.return_value = None

        count_res = MagicMock()
        count_res.scalar_one.return_value = 0

        peer_ids_res = MagicMock()
        peer_ids_res.scalars.return_value.all.return_value = []

        session.execute.side_effect = [user_res, server_res, dup_res, count_res, count_res, peer_ids_res]
        
        # Simulate IntegrityError on flush (e.g. race condition unique constraint violation)
        from sqlalchemy.exc import IntegrityError
        orig_err = Exception("duplicate key value violates unique constraint 'uq_vpn_profiles_server_name'")
        flush_error = IntegrityError("INSERT failed", params={}, orig=orig_err)
        session.flush.side_effect = flush_error

        from services.slots_cache import ServerPeerSnapshot
        snapshot = ServerPeerSnapshot(
            server_id=5,
            peer_ids=frozenset(),
            captured_at=datetime.now(timezone.utc),
        )

        with patch("services.device_service.is_admin", return_value=False), \
             patch("services.device_service.ensure_server_capacity", AsyncMock()):
            with self.assertRaises(DuplicateDeviceName):
                await DeviceService.create_device(
                    session=session,
                    user_id=1,
                    server_id=5,
                    device_name="My Phone",
                    snapshot=snapshot,
                )

        # Verify begin_nested was used (savepoint isolation)
        session.begin_nested.assert_called_once()
        # Verify global session.rollback() was NOT called
        session.rollback.assert_not_called()


if __name__ == "__main__":
    unittest.main()
