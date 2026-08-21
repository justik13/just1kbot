import base64
import json
import os
import struct
import unittest
import zlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from database import connection
from database.models import APIOperation, Server, User, VPNProfile
from services.amnezia_client import (
    AmneziaAPIResult,
    AmneziaClientCreateResponse,
    AmneziaErrorKind,
)
from services.api_operations_executor import execute_claimed_api_operation
from services.api_operations_finalizer import (
    finalize_create_success,
    finalize_delete_success,
    finalize_operation_failure,
    finalize_update_success,
)
from services.api_operations_queue import (
    claim_api_operations,
    recover_stale_api_operations,
)
from utils.vpn_parser import build_conf_file, is_valid_vpn_uri


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "TEST_DATABASE_URL is not set")
class ExecutorPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.sessions.begin() as s:
            await s.execute(
                text(
                    "TRUNCATE account_balance_reservations, "
                    "account_ledger_allocations, account_ledger_entries, "
                    "entitlement_entries, paid_value_ledger, "
                    "tariff_quotes, tariff_versions, payments, api_operations, vpn_profiles, users, servers "
                    "RESTART IDENTITY CASCADE"
                )
            )
            user = User(
                telegram_id=90001,
                subscription_end=datetime.now(timezone.utc) + timedelta(days=1),
                device_limit=2,
            )
            server = Server(
                name="fake", api_url="https://fake", api_key="key", max_clients=10
            )
            s.add_all([user, server])
            await s.flush()
            self.user_id = user.id
            self.server_id = server.id
        self.old_sessionmaker = connection._sessionmaker
        connection._sessionmaker = self.sessions

    async def asyncTearDown(self):
        async with self.sessions.begin() as s:
            await s.execute(
                text(
                    "TRUNCATE account_balance_reservations, "
                    "account_ledger_allocations, account_ledger_entries, "
                    "entitlement_entries, paid_value_ledger, "
                    "tariff_quotes, tariff_versions, payments, api_operations, vpn_profiles, users, servers "
                    "RESTART IDENTITY CASCADE"
                )
            )
        connection._sessionmaker = self.old_sessionmaker
        await self.engine.dispose()

    async def create_claimed(
        self, kind="create_peer", status="pending_create", version=1
    ):
        async with self.sessions.begin() as s:
            p = VPNProfile(
                user_id=self.user_id,
                server_id=self.server_id,
                device_name="phone",
                client_name="tg_90001_p1",
                provisioning_status=status,
                desired_is_active=True,
                desired_version=version,
                is_active=True,
            )
            s.add(p)
            await s.flush()
            op = APIOperation(
                operation_type=kind,
                status="processing",
                idempotency_key=f"{kind}-{p.id}-{version}",
                server_id=self.server_id,
                profile_id=p.id,
                peer_id="peer" if kind != "create_peer" else None,
                client_name=p.client_name,
                payload={"desired_version": version},
                attempts=1,
                locked_by="worker",
                locked_at=datetime.now(timezone.utc),
            )
            s.add(op)
            await s.flush()
            return p.id, op.id

    async def test_create_crash_recovery_is_one_atomic_commit(self):
        pid, oid = await self.create_claimed()
        await finalize_create_success(
            oid,
            worker_id="worker",
            expected_attempt_number=1,
            peer_id="peer",
            raw_config="vpn://config",
            sent_desired_version=1,
            sent_is_active=True,
            sent_expires_at=None,
            session_factory=self.sessions,
        )
        async with self.sessions() as s:
            p = await s.get(VPNProfile, pid)
            op = await s.get(APIOperation, oid)
            self.assertEqual(
                (p.provisioning_status, p.peer_id, op.status),
                ("active", "peer", "succeeded"),
            )

    async def test_follow_up_update_keeps_create_endpoint_snapshot(self):
        pid, oid = await self.create_claimed(version=1)
        async with self.sessions.begin() as s:
            p = await s.get(VPNProfile, pid)
            p.desired_version = 2
            p.desired_is_active = True
            server = await s.get(Server, self.server_id)
            server.api_url = "https://new-endpoint"
            server.api_key = "new-key"

        await finalize_create_success(
            oid,
            worker_id="worker",
            expected_attempt_number=1,
            peer_id="peer",
            raw_config="vpn://config",
            sent_desired_version=1,
            sent_is_active=True,
            sent_expires_at=None,
            server_name_snapshot="fake",
            api_url_snapshot="https://fake",
            api_key_snapshot="key",
            session_factory=self.sessions,
        )

        async with self.sessions() as s:
            update = (
                await s.execute(
                    select(APIOperation).where(
                        APIOperation.operation_type == "update_peer",
                        APIOperation.profile_id == pid,
                    )
                )
            ).scalar_one()
            self.assertEqual(update.api_url_snapshot, "https://fake")
            self.assertEqual(update.api_key_snapshot, "key")

    async def test_update_version_race_records_sent_actual(self):
        pid, oid = await self.create_claimed(
            "update_peer", status="pending_update", version=2
        )
        async with self.sessions.begin() as s:
            p = await s.get(VPNProfile, pid)
            p.desired_version = 3
            p.desired_is_active = True
        await finalize_update_success(
            oid,
            worker_id="worker",
            expected_attempt_number=1,
            sent_version=2,
            sent_is_active=False,
            sent_expires_at=None,
            session_factory=self.sessions,
        )
        async with self.sessions() as s:
            p = await s.get(VPNProfile, pid)
            op = await s.get(APIOperation, oid)
            self.assertFalse(p.actual_is_active)
            self.assertEqual(p.provisioning_status, "pending_update")
            self.assertEqual(op.status, "succeeded")

    async def test_delete_404_finalization_is_atomic(self):
        pid, oid = await self.create_claimed("delete_peer", status="deleting")
        await finalize_delete_success(
            oid,
            worker_id="worker",
            expected_attempt_number=1,
            session_factory=self.sessions,
        )
        async with self.sessions() as s:
            self.assertIsNone(await s.get(VPNProfile, pid))
            self.assertEqual((await s.get(APIOperation, oid)).status, "succeeded")

    def valid_config(self):
        conf = "[Interface]\nPrivateKey = private\nAddress = 10.0.0.2/32\n\n[Peer]\nPublicKey = public\nAllowedIPs = 0.0.0.0/0\nEndpoint = vpn.test:51820\n"
        content = json.dumps(
            {"containers": [{"awg": {"last_config": json.dumps({"config": conf})}}]}
        ).encode()
        uri = "vpn://" + base64.urlsafe_b64encode(
            struct.pack(">I", len(content)) + zlib.compress(content)
        ).decode().rstrip("=")
        self.assertTrue(is_valid_vpn_uri(uri))
        self.assertTrue(build_conf_file(uri))
        return uri

    async def queued_create(self, *, active=True):
        async with self.sessions.begin() as s:
            p = VPNProfile(
                user_id=self.user_id,
                server_id=self.server_id,
                device_name="e2e",
                client_name="exact",
                provisioning_status="pending_create",
                desired_is_active=active,
                desired_version=1,
                is_active=active,
            )
            s.add(p)
            await s.flush()
            op = APIOperation(
                operation_type="create_peer",
                status="pending",
                idempotency_key=f"e2e-{p.id}",
                server_id=self.server_id,
                profile_id=p.id,
                server_name_snapshot="fake",
                api_url_snapshot="https://fake",
                api_key_snapshot="key",
                client_name="exact",
                payload={"desired_version": 1},
            )
            s.add(op)
            await s.flush()
            return p.id, op.id

    async def claim_one(self):
        return (
            await claim_api_operations(
                worker_id="e2e", limit=1, session_factory=self.sessions
            )
        )[0]

    async def ready_retry(self, oid):
        async with self.sessions.begin() as s:
            op = await s.get(APIOperation, oid)
            op.next_attempt_at = datetime.now(timezone.utc)

    async def test_ambiguous_create_reconciles_without_duplicate(self):
        pid, oid = await self.queued_create()
        config = self.valid_config()

        class Fake:
            def __init__(self):
                self.peers = {}
                self.posts = self.deletes = 0

            async def get_all_clients(self):
                return [
                    SimpleNamespace(id=i, clientName=n) for i, n in self.peers.items()
                ]

            async def create_user_result(inner, name, expires):
                inner.posts += 1
                peer = f"p{inner.posts}"
                inner.peers[peer] = name
                if inner.posts == 1:
                    return AmneziaAPIResult(
                        False, None, AmneziaErrorKind.TIMEOUT, None, True, True
                    )
                return AmneziaAPIResult(
                    True,
                    AmneziaClientCreateResponse(id=peer, config=config),
                    None,
                    201,
                    False,
                    False,
                )

            async def delete_user_result(inner, peer):
                inner.deletes += 1
                inner.peers.pop(peer, None)
                return AmneziaAPIResult(True, None, None, 204, False, False)

        fake = Fake()
        with patch("services.api_operations_executor._client", return_value=fake):
            await execute_claimed_api_operation(await self.claim_one())
            async with self.sessions() as db:
                self.assertEqual((await db.get(APIOperation, oid)).status, "retry")
            await self.ready_retry(oid)
            await execute_claimed_api_operation(await self.claim_one())
        async with self.sessions() as db:
            profile = await db.get(VPNProfile, pid)
            operation = await db.get(APIOperation, oid)
            self.assertEqual(profile.provisioning_status, "active")
            self.assertEqual(operation.status, "succeeded")
        self.assertEqual(fake.posts, 2)
        self.assertEqual(fake.deletes, 1)
        self.assertEqual(list(fake.peers.values()), ["exact"])

    async def test_invalid_config_cleanup_retry(self):
        pid, oid = await self.queued_create()

        class Fake:
            def __init__(self):
                self.peers = {"bad": "exact"}
                self.posts = 0
                self.deletes = 0

            async def get_all_clients(self):
                return [
                    SimpleNamespace(id=i, clientName=n) for i, n in self.peers.items()
                ]

            async def create_user_result(inner, name, expires):
                inner.posts += 1
                return AmneziaAPIResult(
                    True,
                    AmneziaClientCreateResponse(id="bad", config="invalid"),
                    None,
                    201,
                    False,
                    False,
                )

            async def delete_user_result(inner, peer):
                inner.deletes += 1
                if inner.deletes == 1:
                    return AmneziaAPIResult(
                        False, None, AmneziaErrorKind.NETWORK_ERROR, None, True, False
                    )
                inner.peers.pop(peer, None)
                return AmneziaAPIResult(True, None, None, 204, False, False)

        fake = Fake()
        with patch("services.api_operations_executor._client", return_value=fake):
            await execute_claimed_api_operation(await self.claim_one())
            await self.ready_retry(oid)
            await execute_claimed_api_operation(await self.claim_one())
        async with self.sessions() as db:
            self.assertEqual(
                (await db.get(VPNProfile, pid)).provisioning_status, "create_failed"
            )
        self.assertEqual(fake.posts, 1)
        self.assertFalse(fake.peers)

    async def test_profile_missing_after_post_is_compensated(self):
        pid, oid = await self.queued_create()
        config = self.valid_config()
        sessions = self.sessions

        class Fake:
            def __init__(self):
                self.peers = {}
                self.deletes = 0

            async def create_user_result(inner, name, expires):
                inner.peers["made"] = name
                async with sessions.begin() as db:
                    profile = await db.get(VPNProfile, pid)
                    await db.delete(profile)
                return AmneziaAPIResult(
                    True,
                    AmneziaClientCreateResponse(id="made", config=config),
                    None,
                    201,
                    False,
                    False,
                )

            async def delete_user_result(inner, peer):
                inner.deletes += 1
                inner.peers.pop(peer, None)
                return AmneziaAPIResult(True, None, None, 204, False, False)

            async def get_all_clients(self):
                return []

        fake = Fake()
        with patch("services.api_operations_executor._client", return_value=fake):
            await execute_claimed_api_operation(await self.claim_one())
        self.assertFalse(fake.peers)
        self.assertEqual(fake.deletes, 1)

    async def test_inactive_pending_create_does_not_post(self):
        pid, oid = await self.queued_create(active=False)
        fake = SimpleNamespace(
            create_user_result=unittest.mock.AsyncMock(),
            get_all_clients=unittest.mock.AsyncMock(return_value=[]),
        )
        with patch("services.api_operations_executor._client", return_value=fake):
            await execute_claimed_api_operation(await self.claim_one())
        fake.create_user_result.assert_not_awaited()
        async with self.sessions() as db:
            self.assertIsNone(await db.get(VPNProfile, pid))

    async def test_terminal_create_failure_updates_profile(self):
        pid, oid = await self.create_claimed("create_peer")
        await finalize_operation_failure(
            oid,
            worker_id="worker",
            expected_attempt_number=1,
            retryable=False,
            error_code="validation_failed",
            error_message="safe",
            session_factory=self.sessions,
        )
        async with self.sessions() as db:
            self.assertEqual(
                (await db.get(VPNProfile, pid)).provisioning_status, "create_failed"
            )

    async def test_terminal_update_failure_updates_profile(self):
        pid, oid = await self.create_claimed("update_peer", status="pending_update")
        await finalize_operation_failure(
            oid,
            worker_id="worker",
            expected_attempt_number=1,
            retryable=False,
            error_code="auth_failed",
            error_message="safe",
            session_factory=self.sessions,
        )
        async with self.sessions() as db:
            self.assertEqual(
                (await db.get(VPNProfile, pid)).provisioning_status, "update_failed"
            )

    async def test_update_success_preserves_deleting(self):
        pid, oid = await self.create_claimed("update_peer", status="deleting")
        await finalize_update_success(
            oid,
            worker_id="worker",
            expected_attempt_number=1,
            sent_version=1,
            sent_is_active=True,
            sent_expires_at=None,
            session_factory=self.sessions,
        )
        async with self.sessions() as db:
            self.assertEqual(
                (await db.get(VPNProfile, pid)).provisioning_status, "deleting"
            )

    async def test_cleanup_does_not_delete_different_peer_with_same_name(self):
        async with self.sessions.begin() as db:
            p = VPNProfile(
                user_id=self.user_id,
                server_id=self.server_id,
                device_name="cleanup",
                client_name="exact",
                provisioning_status="create_cleanup_pending",
                desired_is_active=False,
                desired_version=1,
                is_active=False,
            )
            db.add(p)
            await db.flush()
            op = APIOperation(
                operation_type="create_peer",
                status="retry",
                idempotency_key=f"cleanup-{p.id}",
                server_id=self.server_id,
                profile_id=p.id,
                server_name_snapshot="fake",
                api_url_snapshot="https://fake",
                api_key_snapshot="key",
                peer_id="saved-missing",
                client_name="exact",
                payload={},
                attempts=1,
                next_attempt_at=datetime.now(timezone.utc),
                last_error_code="invalid_created_config_cleanup",
            )
            db.add(op)
            await db.flush()
            oid = op.id
        fake = SimpleNamespace(
            get_all_clients=unittest.mock.AsyncMock(
                return_value=[SimpleNamespace(id="manual", clientName="exact")]
            ),
            delete_user_result=unittest.mock.AsyncMock(),
        )
        with patch("services.api_operations_executor._client", return_value=fake):
            await execute_claimed_api_operation(await self.claim_one())
        fake.delete_user_result.assert_not_awaited()
        async with self.sessions() as db:
            self.assertEqual(
                (await db.get(APIOperation, oid)).last_error_code,
                "cleanup_peer_identity_mismatch",
            )

    async def test_stale_create_becomes_cleanup_pending(self):
        pid, oid = await self.create_claimed("create_peer")
        async with self.sessions.begin() as db:
            op = await db.get(APIOperation, oid)
            op.max_attempts = 1
            op.locked_at = datetime.now(timezone.utc) - timedelta(hours=1)
        await recover_stale_api_operations(
            lease_timeout=timedelta(minutes=5), session_factory=self.sessions
        )
        async with self.sessions() as db:
            self.assertEqual((await db.get(APIOperation, oid)).status, "dead")
            self.assertEqual(
                (await db.get(VPNProfile, pid)).provisioning_status,
                "create_cleanup_pending",
            )

    async def test_profile_missing_cleanup_does_not_delete_different_id(self):
        async with self.sessions.begin() as db:
            op = APIOperation(
                operation_type="create_peer",
                status="retry",
                idempotency_key="missing-identity",
                server_id=self.server_id,
                profile_id=None,
                server_name_snapshot="fake",
                api_url_snapshot="https://fake",
                api_key_snapshot="key",
                peer_id="old-peer",
                client_name="exact",
                payload={},
                attempts=1,
                next_attempt_at=datetime.now(timezone.utc),
                last_error_code="create_compensation_required",
            )
            db.add(op)
            await db.flush()
            oid = op.id
        fake = SimpleNamespace(
            get_all_clients=unittest.mock.AsyncMock(
                return_value=[SimpleNamespace(id="manual-peer", clientName="exact")]
            ),
            delete_user_result=unittest.mock.AsyncMock(),
        )
        with patch("services.api_operations_executor._client", return_value=fake):
            await execute_claimed_api_operation(await self.claim_one())
        fake.delete_user_result.assert_not_awaited()
        async with self.sessions() as db:
            op = await db.get(APIOperation, oid)
            self.assertEqual(op.status, "dead")
            self.assertEqual(op.last_error_code, "cleanup_peer_identity_mismatch")

    async def test_saved_id_name_mismatch_requires_manual_review(self):
        async with self.sessions.begin() as db:
            p = VPNProfile(
                user_id=self.user_id,
                server_id=self.server_id,
                device_name="mismatch",
                client_name="expected",
                provisioning_status="create_cleanup_pending",
                desired_is_active=False,
                desired_version=1,
                is_active=False,
            )
            db.add(p)
            await db.flush()
            op = APIOperation(
                operation_type="create_peer",
                status="retry",
                idempotency_key="saved-mismatch",
                server_id=self.server_id,
                profile_id=p.id,
                server_name_snapshot="fake",
                api_url_snapshot="https://fake",
                api_key_snapshot="key",
                peer_id="saved",
                client_name="expected",
                payload={},
                attempts=1,
                next_attempt_at=datetime.now(timezone.utc),
                last_error_code="invalid_created_config_cleanup",
            )
            db.add(op)
        fake = SimpleNamespace(
            get_all_clients=unittest.mock.AsyncMock(
                return_value=[SimpleNamespace(id="saved", clientName="other")]
            ),
            delete_user_result=unittest.mock.AsyncMock(),
        )
        with patch("services.api_operations_executor._client", return_value=fake):
            await execute_claimed_api_operation(await self.claim_one())
        fake.delete_user_result.assert_not_awaited()
