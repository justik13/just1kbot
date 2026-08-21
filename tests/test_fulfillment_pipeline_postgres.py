import os
import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database.models import APIOperation, Server, User, VPNProfile
from services.api_operations_queue import ensure_delete_operation
from services.device_service import DeviceService, ServerUnavailable
from services.profile_deletion_service import ProfileDeletionService
from services.slots_cache import ServerPeerSnapshot


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "TEST_DATABASE_URL is not set")
class FulfillmentPipelinePostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.sessions.begin() as s:
            await s.execute(
                text(
                    "TRUNCATE account_balance_reservations, "
                    "account_ledger_allocations, account_ledger_entries, "
                    "entitlement_entries, paid_value_ledger, "
                    "tariff_quotes, tariff_versions, payments, api_operations, vpn_profiles, users, servers, system_settings, payment_disputes "

                    "RESTART IDENTITY CASCADE"
                )
            )
            u = User(
                telegram_id=91001,
                device_limit=20,
                subscription_end=datetime.now(timezone.utc) + timedelta(days=1),
            )
            v = Server(name="s", api_url="https://fake", api_key="key", max_clients=10)
            s.add_all([u, v])
            await s.flush()
            self.uid = u.id
            self.sid = v.id

    async def asyncTearDown(self):
        async with self.sessions.begin() as s:
            await s.execute(
                text(
                    "TRUNCATE account_balance_reservations, "
                    "account_ledger_allocations, account_ledger_entries, "
                    "entitlement_entries, paid_value_ledger, "
                    "tariff_quotes, tariff_versions, payments, api_operations, vpn_profiles, users, servers, system_settings, payment_disputes "

                    "RESTART IDENTITY CASCADE"
                )
            )
        await self.engine.dispose()

    async def test_ban_during_create_cancels_pending_without_peer(self):
        async with self.sessions.begin() as s:
            p = VPNProfile(
                user_id=self.uid,
                server_id=self.sid,
                device_name="p",
                client_name="tg_91001_p",
                provisioning_status="pending_create",
                desired_version=1,
                desired_is_active=True,
                is_active=True,
            )
            s.add(p)
            await s.flush()
            op = APIOperation(
                operation_type="create_peer",
                idempotency_key=f"create-peer:{p.id}:v1",
                server_id=self.sid,
                profile_id=p.id,
                client_name=p.client_name,
                payload={},
                status="pending",
            )
            s.add(op)
            await s.flush()
            pid = p.id
            oid = op.id
            await ProfileDeletionService.delete_profiles_list(
                s, [p], reason="ban_delete"
            )
        async with self.sessions() as s:
            self.assertIsNone(await s.get(VPNProfile, pid))
            self.assertEqual((await s.get(APIOperation, oid)).status, "cancelled")

    async def test_delete_dead_retry_and_reason_is_not_identity(self):
        async with self.sessions.begin() as s:
            p = VPNProfile(
                user_id=self.uid,
                server_id=self.sid,
                device_name="p",
                peer_id="peer",
                raw_config="vpn://x",
                client_name="exact",
                provisioning_status="deleting",
                desired_version=1,
                desired_is_active=False,
                is_active=False,
            )
            s.add(p)
            await s.flush()
            first = await ensure_delete_operation(
                s,
                idempotency_key=f"delete-peer:{p.id}:peer",
                server_id=self.sid,
                profile_id=p.id,
                server_name_snapshot="s",
                api_url_snapshot="https://fake",
                api_key_snapshot="key",
                peer_id="peer",
                audit_reason="device_delete",
            )
            first.status = "dead"
            first.completed_at = datetime.now(timezone.utc)
            oid = first.id
        async with self.sessions.begin() as s:
            second = await ensure_delete_operation(
                s,
                idempotency_key=f"delete-peer:{p.id}:peer",
                server_id=self.sid,
                profile_id=p.id,
                server_name_snapshot="s",
                api_url_snapshot="https://fake",
                api_key_snapshot="key",
                peer_id="peer",
                audit_reason="chargeback_delete",
            )
            self.assertEqual(second.id, oid)
            self.assertEqual(second.status, "retry")
            self.assertEqual(second.payload, {"managed_workflow": True})

    async def test_grace_cleanup_reloads_profile_for_update(self):
        async with self.sessions.begin() as s:
            p = VPNProfile(
                user_id=self.uid,
                server_id=self.sid,
                device_name="race",
                peer_id="peer-race",
                raw_config="vpn://x",
                client_name="exact",
                provisioning_status="active",
                desired_version=1,
                desired_is_active=True,
                is_active=True,
            )
            s.add(p)
            await s.flush()
            stale = p
            pid = p.id
        async with self.sessions.begin() as s:
            await ProfileDeletionService.delete_profiles_list(
                s, [stale], reason="grace_delete"
            )
        async with self.sessions() as s:
            p = await s.get(VPNProfile, pid)
            op = (
                await s.execute(
                    __import__("sqlalchemy")
                    .select(APIOperation)
                    .where(
                        APIOperation.profile_id == pid,
                        APIOperation.operation_type == "delete_peer",
                    )
                )
            ).scalar_one()
            self.assertEqual(p.provisioning_status, "deleting")
            self.assertEqual(op.status, "pending")

    async def test_manual_peer_capacity_uses_exact_ids(self):
        async with self.sessions.begin() as s:
            server = await s.get(Server, self.sid)
            server.max_clients = 10
            for i in range(8):
                s.add(
                    VPNProfile(
                        user_id=self.uid,
                        server_id=self.sid,
                        device_name=f"d{i}",
                        peer_id=f"b{i}",
                        raw_config="vpn://x",
                        client_name=f"c{i}",
                        provisioning_status="active",
                        desired_version=1,
                        desired_is_active=True,
                        is_active=True,
                    )
                )
        snap = ServerPeerSnapshot(
            self.sid,
            frozenset({*(f"b{i}" for i in range(8)), "manual-1", "manual-2"}),
            datetime.now(timezone.utc),
        )
        async with self.sessions.begin() as s:
            with self.assertRaises(ServerUnavailable):
                await DeviceService.create_device(
                    s,
                    user_id=self.uid,
                    server_id=self.sid,
                    device_name="new",
                    snapshot=snap,
                )

    async def test_server_delete_serializes_with_create(self):
        import asyncio

        locked = asyncio.Event()
        release = asyncio.Event()

        async def deleting():
            async with self.sessions.begin() as s:
                server = (
                    await s.execute(
                        __import__("sqlalchemy")
                        .select(Server)
                        .where(Server.id == self.sid)
                        .with_for_update()
                    )
                ).scalar_one()
                locked.set()
                await release.wait()
                await s.delete(server)

        async def creating_side():
            await locked.wait()
            async with self.sessions.begin() as s:
                return (
                    await s.execute(
                        __import__("sqlalchemy")
                        .select(Server)
                        .where(Server.id == self.sid)
                        .with_for_update()
                    )
                ).scalar_one_or_none()

        first = asyncio.create_task(deleting())
        second = asyncio.create_task(creating_side())
        await locked.wait()
        await asyncio.sleep(0.02)
        self.assertFalse(second.done())
        release.set()
        await first
        self.assertIsNone(await second)

    async def test_retry_ambiguous_create_is_not_cancelled_by_ban(self):
        async with self.sessions.begin() as s:
            p = VPNProfile(
                user_id=self.uid,
                server_id=self.sid,
                device_name="ambiguous",
                client_name="exact",
                provisioning_status="pending_create",
                desired_version=1,
                desired_is_active=True,
                is_active=True,
            )
            s.add(p)
            await s.flush()
            op = APIOperation(
                operation_type="create_peer",
                idempotency_key=f"ambiguous-{p.id}",
                server_id=self.sid,
                profile_id=p.id,
                client_name="exact",
                payload={},
                status="retry",
                attempts=1,
                last_error_code="create_ambiguous_reconcile",
                next_attempt_at=datetime.now(timezone.utc),
            )
            s.add(op)
            await s.flush()
            pid = p.id
            oid = op.id
            await ProfileDeletionService.delete_profiles_list(
                s, [p], reason="ban_delete"
            )
        async with self.sessions() as s:
            self.assertEqual(
                (await s.get(VPNProfile, pid)).provisioning_status, "deleting"
            )
            self.assertEqual((await s.get(APIOperation, oid)).status, "retry")

    async def test_dead_create_cleanup_blocks_server_delete(self):
        from bot.handlers.admin.servers.delete_routes import (
            _has_unfinished_create_cleanup,
        )

        async with self.sessions.begin() as s:
            p = VPNProfile(
                user_id=self.uid,
                server_id=self.sid,
                device_name="cleanup-block",
                client_name="exact",
                provisioning_status="create_cleanup_pending",
                desired_version=1,
                desired_is_active=False,
                is_active=False,
            )
            s.add(p)
            await s.flush()
            op = APIOperation(
                operation_type="create_peer",
                status="dead",
                idempotency_key="dead-cleanup",
                server_id=self.sid,
                profile_id=p.id,
                peer_id="orphan",
                client_name="exact",
                payload={},
                attempts=10,
                last_error_code="max_attempts_exhausted",
            )
            s.add(op)
            await s.flush()
            self.assertTrue(_has_unfinished_create_cleanup([p], [op]))
        async with self.sessions() as s:
            self.assertIsNotNone(await s.get(Server, self.sid))
            self.assertIsNotNone(await s.get(VPNProfile, p.id))
            self.assertIsNotNone(await s.get(APIOperation, op.id))
