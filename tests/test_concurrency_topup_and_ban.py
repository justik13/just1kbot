import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from sqlalchemy.ext.asyncio import AsyncSession

from services.account_topup import cancel_all_unfinished_topups
from services.ban_service import BanService
from database.models import Payment


class TestConcurrencyTopupAndBan(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_all_unfinished_topups_rereads_under_advisory_lock(self):
        session = MagicMock(spec=AsyncSession)
        
        # First query (before advisory lock): returns payment 1
        # Second query (after advisory lock): returns payments 1 and 2 (payment 2 arrived concurrently)
        p1 = Payment(id=1, user_id=10, provider_status='creating', checkout_status='active', ui_visible=True)
        p2 = Payment(id=2, user_id=10, provider_status='pending', checkout_status='active', ui_visible=True)

        scalar_results = [
            MagicMock(all=MagicMock(return_value=[p1])),
            MagicMock(all=MagicMock(return_value=[p1, p2])),
        ]
        session.scalars = AsyncMock(side_effect=scalar_results)
        session.add = MagicMock()
        session.flush = AsyncMock()

        with patch('services.account_topup.lock_checkout_user', new=AsyncMock(return_value=SimpleNamespace(id=10))):
            count = await cancel_all_unfinished_topups(session, user_id=10)

            # Both payments must be canceled because re-read caught the concurrent payment
            self.assertEqual(count, 2)
            self.assertEqual(p1.provider_status, 'canceled')
            self.assertEqual(p2.provider_status, 'canceled')
            self.assertFalse(p1.ui_visible)
            self.assertFalse(p2.ui_visible)

    async def test_ban_user_rereads_payments_under_advisory_lock(self):
        session = MagicMock(spec=AsyncSession)
        user = SimpleNamespace(id=10, telegram_id=12345, is_banned=False, is_deleted=False)

        # First query: returns [1]
        # Second query under advisory lock: returns [1, 2]
        scalar_results = [
            MagicMock(all=MagicMock(return_value=[1])),
            MagicMock(all=MagicMock(return_value=[1, 2])),
        ]
        session.scalars = AsyncMock(side_effect=scalar_results)
        session.execute = AsyncMock()
        session.scalar = AsyncMock(return_value=user)
        
        p1 = Payment(id=1, user_id=10, provider_status='creating', checkout_status='active', ui_visible=True, external_id=None)
        p2 = Payment(id=2, user_id=10, provider_status='pending', checkout_status='active', ui_visible=True, external_id=None)
        session.get = AsyncMock(side_effect=lambda model, pid: {1: p1, 2: p2}.get(pid))

        with patch('services.ban_service.update_user', new=AsyncMock()), \
             patch('services.ban_service.ProfileDeletionService.delete_profiles_for_user', new=AsyncMock(return_value=0)), \
             patch('services.ban_service.AuditService.log_action', new=AsyncMock()), \
             patch('services.ban_service.invalidate_user_cache'):

            ok, msg = await BanService._ban_user(session, admin_id=1, user=user, telegram_id=12345)

            self.assertTrue(ok)
            self.assertEqual(p1.checkout_status, 'abandoned')
            self.assertEqual(p2.checkout_status, 'abandoned')
            self.assertFalse(p1.ui_visible)
            self.assertFalse(p2.ui_visible)


if __name__ == '__main__':
    unittest.main()
