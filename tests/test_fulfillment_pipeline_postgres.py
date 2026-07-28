import os, unittest
from datetime import datetime, timezone
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from database.models import APIOperation, Server, User, VPNProfile
from services.api_operations_queue import ensure_delete_operation
from services.profile_deletion_service import ProfileDeletionService

@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "TEST_DATABASE_URL is not set")
class FulfillmentPipelinePostgresTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.engine=create_async_engine(os.environ["TEST_DATABASE_URL"]); self.sessions=async_sessionmaker(self.engine,expire_on_commit=False)
  async with self.sessions.begin() as s:
   await s.execute(delete(APIOperation)); await s.execute(delete(VPNProfile)); await s.execute(delete(User)); await s.execute(delete(Server))
   u=User(telegram_id=91001,device_limit=2); v=Server(name="s",api_url="https://fake",api_key="key",max_clients=10); s.add_all([u,v]); await s.flush(); self.uid=u.id; self.sid=v.id
 async def asyncTearDown(self):
  async with self.sessions.begin() as s:
   await s.execute(delete(APIOperation)); await s.execute(delete(VPNProfile)); await s.execute(delete(User)); await s.execute(delete(Server))
  await self.engine.dispose()
 async def test_ban_during_create_cancels_pending_without_peer(self):
  async with self.sessions.begin() as s:
   p=VPNProfile(user_id=self.uid,server_id=self.sid,device_name="p",client_name="tg_91001_p",provisioning_status="pending_create",desired_version=1,desired_is_active=True,is_active=True); s.add(p); await s.flush()
   op=APIOperation(operation_type="create_peer",idempotency_key=f"create-peer:{p.id}:v1",server_id=self.sid,profile_id=p.id,client_name=p.client_name,payload={},status="pending"); s.add(op); await s.flush(); pid=p.id; oid=op.id
   await ProfileDeletionService.delete_profiles_list(s,[p],reason="ban_delete")
  async with self.sessions() as s:
   self.assertIsNone(await s.get(VPNProfile,pid)); self.assertEqual((await s.get(APIOperation,oid)).status,"cancelled")
 async def test_delete_dead_retry_and_reason_is_not_identity(self):
  async with self.sessions.begin() as s:
   p=VPNProfile(user_id=self.uid,server_id=self.sid,device_name="p",peer_id="peer",raw_config="vpn://x",client_name="exact",provisioning_status="deleting",desired_version=1,desired_is_active=False,is_active=False); s.add(p); await s.flush()
   first=await ensure_delete_operation(s,idempotency_key=f"delete-peer:{p.id}:peer",server_id=self.sid,profile_id=p.id,server_name_snapshot="s",api_url_snapshot="https://fake",api_key_snapshot="key",peer_id="peer",audit_reason="device_delete"); first.status="dead"; first.completed_at=datetime.now(timezone.utc); oid=first.id
  async with self.sessions.begin() as s:
   second=await ensure_delete_operation(s,idempotency_key=f"delete-peer:{p.id}:peer",server_id=self.sid,profile_id=p.id,server_name_snapshot="s",api_url_snapshot="https://fake",api_key_snapshot="key",peer_id="peer",audit_reason="chargeback_delete")
   self.assertEqual(second.id,oid); self.assertEqual(second.status,"retry"); self.assertEqual(second.payload,{"managed_workflow":True})
