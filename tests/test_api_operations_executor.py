import os
import unittest
from datetime import datetime, timedelta, timezone
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from database.models import APIOperation, Server, User, VPNProfile
from services.api_operations_finalizer import finalize_create_success, finalize_update_success, finalize_delete_success

@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "TEST_DATABASE_URL is not set")
class ExecutorPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine=create_async_engine(os.environ["TEST_DATABASE_URL"])
        self.sessions=async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.sessions.begin() as s:
            await s.execute(delete(APIOperation)); await s.execute(delete(VPNProfile)); await s.execute(delete(User)); await s.execute(delete(Server))
            user=User(telegram_id=90001, subscription_end=datetime.now(timezone.utc)+timedelta(days=1), device_limit=2)
            server=Server(name="fake", api_url="https://fake", api_key="key", max_clients=10)
            s.add_all([user,server]); await s.flush(); self.user_id=user.id; self.server_id=server.id
    async def asyncTearDown(self):
        async with self.sessions.begin() as s:
            await s.execute(delete(APIOperation)); await s.execute(delete(VPNProfile)); await s.execute(delete(User)); await s.execute(delete(Server))
        await self.engine.dispose()
    async def create_claimed(self, kind="create_peer", status="pending_create", version=1):
        async with self.sessions.begin() as s:
            p=VPNProfile(user_id=self.user_id,server_id=self.server_id,device_name="phone",client_name="tg_90001_p1",provisioning_status=status,desired_is_active=True,desired_version=version,is_active=True)
            s.add(p); await s.flush()
            op=APIOperation(operation_type=kind,status="processing",idempotency_key=f"{kind}-{p.id}-{version}",server_id=self.server_id,profile_id=p.id,peer_id="peer" if kind!="create_peer" else None,client_name=p.client_name,payload={"desired_version":version},attempts=1,locked_by="worker",locked_at=datetime.now(timezone.utc))
            s.add(op); await s.flush(); return p.id,op.id
    async def test_create_crash_recovery_is_one_atomic_commit(self):
        pid,oid=await self.create_claimed()
        await finalize_create_success(oid,worker_id="worker",expected_attempt_number=1,peer_id="peer",raw_config="vpn://config",sent_desired_version=1,sent_is_active=True,sent_expires_at=None,session_factory=self.sessions)
        async with self.sessions() as s:
            p=await s.get(VPNProfile,pid); op=await s.get(APIOperation,oid)
            self.assertEqual((p.provisioning_status,p.peer_id,op.status),("active","peer","succeeded"))
    async def test_update_version_race_records_sent_actual(self):
        pid,oid=await self.create_claimed("update_peer",status="pending_update",version=2)
        async with self.sessions.begin() as s:
            p=await s.get(VPNProfile,pid); p.desired_version=3; p.desired_is_active=True
        await finalize_update_success(oid,worker_id="worker",expected_attempt_number=1,sent_version=2,sent_is_active=False,sent_expires_at=None,session_factory=self.sessions)
        async with self.sessions() as s:
            p=await s.get(VPNProfile,pid); op=await s.get(APIOperation,oid)
            self.assertFalse(p.actual_is_active); self.assertEqual(p.provisioning_status,"pending_update"); self.assertEqual(op.status,"succeeded")
    async def test_delete_404_finalization_is_atomic(self):
        pid,oid=await self.create_claimed("delete_peer",status="deleting")
        await finalize_delete_success(oid,worker_id="worker",expected_attempt_number=1,session_factory=self.sessions)
        async with self.sessions() as s:
            self.assertIsNone(await s.get(VPNProfile,pid)); self.assertEqual((await s.get(APIOperation,oid)).status,"succeeded")
