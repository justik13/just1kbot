"""Unit and contract tests for Admin Mini App QOL backend endpoints and services."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import timedelta
from decimal import Decimal
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.miniapp import handlers_admin
from bot.miniapp.handlers_user import _serialize_ledger_entry
from config.enums import (
    AccountLedgerEntryType,
    ServerHealthState,
    ServerLifecycleStatus,
    WebhookInboxStatus,
    WhiteInternetProvisioningStatus,
    WhiteInternetStatus,
)
from database.repositories import account_ledger_repo
from services import admin_nodes_service, broadcast_service, white_internet_service
from services.admin_nodes_service import AdminNodeServiceError
from services.broadcast_service import BroadcastConflictError, serialize_broadcast
from utils.datetime_helpers import now_utc


def _make_session_scope(session: AsyncMock):
    @asynccontextmanager
    async def _scope():
        yield session

    return _scope


@pytest.mark.asyncio
async def test_forgive_real_debt_and_serialize_ledger_entry():
    fake_entry = SimpleNamespace(
        id=42,
        user_id=7,
        entry_type=AccountLedgerEntryType.PAYMENT_CREDIT,
        bucket="real",
        amount=Decimal("250.00"),
        currency="RUB",
        order_id=None,
        metadata_={
            "source": "admin_debt_forgiveness",
            "admin_telegram_id": 999,
            "note": "support forgiveness",
        },
        created_at=now_utc(),
    )
    session = AsyncMock()
    with patch.object(
        account_ledger_repo,
        "_insert_or_get_entry",
        AsyncMock(return_value=(fake_entry, True)),
    ) as insert_mock:
        entry = await account_ledger_repo.forgive_real_debt(
            session,
            user_id=7,
            amount_rub=250,
            admin_telegram_id=999,
            note="support forgiveness",
        )

    assert entry is fake_entry
    insert_mock.assert_awaited_once()
    values = insert_mock.call_args.kwargs["values"]
    assert values["user_id"] == 7
    assert values["entry_type"] == AccountLedgerEntryType.PAYMENT_CREDIT
    assert values["bucket"] == "real"
    assert values["amount"] == Decimal("250.00")
    assert values["currency"] == "RUB"
    assert values["order_id"] is None
    assert values["idempotency_key"].startswith("miniapp:admin:forgive_debt:999:7:")
    assert values["metadata_"] == {
        "source": "admin_debt_forgiveness",
        "admin_telegram_id": 999,
        "note": "support forgiveness",
    }

    serialized = _serialize_ledger_entry(fake_entry)
    assert serialized["id"] == 42
    assert serialized["type"] == "payment_credit"
    assert serialized["amount_rub"] == 250
    assert serialized["description"] == "Погашение долга"


@pytest.mark.asyncio
async def test_api_admin_overview_handler_contract():
    admin_ctx = SimpleNamespace(telegram_id=1001)
    session = AsyncMock()
    session.scalar = AsyncMock(side_effect=[None, 3, 2])

    nodes = [
        {
            "id": 1,
            "name": "RU Hub 1",
            "role": "ru_hub",
            "is_active": True,
            "health_state": ServerHealthState.ONLINE,
            "disabled_reason": None,
            "ingress_problem": False,
            "cdn_problem": False,
            "module_errors_count": 0,
        },
        {
            "id": 2,
            "name": "Manual Disabled Node",
            "role": "exit_node",
            "is_active": False,
            "health_state": ServerHealthState.MANUAL_DISABLED,
            "disabled_reason": "manual",
            "ingress_problem": False,
            "cdn_problem": False,
            "module_errors_count": 0,
        },
        {
            "id": 3,
            "name": "Problem Node",
            "role": "ru_hub",
            "is_active": True,
            "health_state": ServerHealthState.PROBLEM,
            "disabled_reason": None,
            "ingress_problem": True,
            "cdn_problem": False,
            "module_errors_count": 2,
        },
        {
            "id": 4,
            "name": "Degraded Node",
            "role": "exit_node",
            "is_active": True,
            "health_state": ServerHealthState.WAITING_CONFIRMATION,
            "disabled_reason": None,
            "ingress_problem": False,
            "cdn_problem": False,
            "module_errors_count": 0,
        },
    ]
    worker_snapshot = {"node_monitor": {"status": "ok", "last_tick_age_s": 5}}

    with (
        patch.object(handlers_admin, "require_admin_user", return_value=(admin_ctx, None)),
        patch.object(handlers_admin, "session_scope", _make_session_scope(session)),
        patch.object(handlers_admin.admin_nodes_service, "list_nodes", AsyncMock(return_value=nodes)),
        patch.object(handlers_admin, "get_system_setting", AsyncMock(return_value="false")),
        patch.object(
            handlers_admin.admin_tools_service,
            "get_overview_finances_summary",
            AsyncMock(return_value={"today_rub": 500}),
        ),
        patch.object(
            handlers_admin.users_repo,
            "get_user_filter_counts",
            AsyncMock(return_value={"all": 20, "active_standard": 10, "active_wi": 4, "expiring_3d": 1}),
        ),
        patch.object(handlers_admin, "get_worker_health_snapshot", return_value=worker_snapshot),
    ):
        resp = await handlers_admin.api_admin_overview_handler(SimpleNamespace())

    assert resp.status == 200
    data = json.loads(resp.text)
    assert data["ok"] is True
    assert data["nodes"]["total"] == 4
    assert data["nodes"]["online"] == 1
    assert data["nodes"]["degraded"] == 1
    assert data["nodes"]["offline"] == 1
    assert data["nodes"]["disabled"] == 1
    problem_ids = [p["id"] for p in data["nodes"]["problems"]]
    assert problem_ids == [3, 4]
    assert all(p["health_state"] != ServerHealthState.MANUAL_DISABLED for p in data["nodes"]["problems"])
    assert data["webhooks"] == {"pending": 3, "dead": 2}
    assert data["workers"] == worker_snapshot


@pytest.mark.asyncio
async def test_api_admin_user_device_delete_handler_wi_expired_vs_active():
    admin_ctx = SimpleNamespace(telegram_id=1001)
    request = SimpleNamespace(match_info={"user_id": "42", "hwid": "hw-device-1"})

    async def _run_case(wi_status: WhiteInternetStatus, wi_prov: WhiteInternetProvisioningStatus):
        session = AsyncMock()
        user = SimpleNamespace(id=42, is_deleted=False)
        session.scalar = AsyncMock(return_value=user)
        deleted_dev = SimpleNamespace(device_uuid="dev-uuid-123")
        vless_sub = SimpleNamespace(id=11, version=3)
        wi_sub = SimpleNamespace(
            id=22,
            status=wi_status,
            provisioning_status=wi_prov,
            desired_version=5,
        )
        queue_wi_mock = AsyncMock()
        deprovision_mock = MagicMock()
        wi_dispatch_mock = MagicMock()
        wi_hub = SimpleNamespace(
            id=99,
            api_url="https://ru-hub.example",
            api_key="key",
            protocol="xray",
        )

        with (
            patch.object(handlers_admin, "require_admin_user", return_value=(admin_ctx, None)),
            patch.object(handlers_admin, "session_scope", _make_session_scope(session)),
            patch.object(handlers_admin, "acquire_user_account_lock", AsyncMock()),
            patch.object(
                handlers_admin.user_devices_repo,
                "delete_user_device",
                AsyncMock(return_value=deleted_dev),
            ),
            patch.object(
                handlers_admin.vless_subscription_repo,
                "get_subscription_by_user_id",
                AsyncMock(return_value=vless_sub),
            ),
            patch.object(
                handlers_admin.vless_subscription_repo,
                "add_pending_revocation_uuids",
                AsyncMock(),
            ),
            patch.object(
                handlers_admin.VlessSubscriptionService,
                "deprovision_background",
                deprovision_mock,
            ),
            patch.object(
                handlers_admin.VlessSubscriptionService,
                "ensure_synced_background",
                MagicMock(),
            ),
            patch.object(
                handlers_admin,
                "get_subscription_by_user_id",
                AsyncMock(return_value=wi_sub),
            ),
            patch.object(
                handlers_admin.white_internet_repo,
                "get_subscription_with_lock",
                AsyncMock(return_value=wi_sub),
            ),
            patch.object(
                handlers_admin.white_internet_repo,
                "add_pending_revocation_uuids",
                AsyncMock(),
            ),
            patch.object(
                handlers_admin.servers_repo,
                "get_wi_revocation_ru_hubs",
                AsyncMock(return_value=[wi_hub]),
            ),
            patch.object(white_internet_service, "_dispatch_deprovision", wi_dispatch_mock),
            patch.object(handlers_admin, "_queue_wi_inline_sync", queue_wi_mock),
            patch.object(handlers_admin.AuditService, "log_action", AsyncMock()),
        ):
            resp = await handlers_admin.api_admin_user_device_delete_handler(request)

        return resp, wi_sub, queue_wi_mock, deprovision_mock, wi_dispatch_mock

    # Case 1: EXPIRED WI subscription must stay SYNCED_INACTIVE, bump desired_version for revocation fence, and not queue inline sync
    resp_exp, wi_exp, queue_exp, deprov_exp, wi_disp_exp = await _run_case(
        WhiteInternetStatus.EXPIRED,
        WhiteInternetProvisioningStatus.SYNCED_INACTIVE,
    )
    assert resp_exp.status == 200
    assert wi_exp.provisioning_status == WhiteInternetProvisioningStatus.SYNCED_INACTIVE
    assert wi_exp.desired_version == 6
    queue_exp.assert_not_awaited()
    assert deprov_exp.call_count == 1
    assert wi_disp_exp.call_count == 1
    assert wi_disp_exp.call_args.args[2] == 6

    # Case 2: ACTIVE WI subscription bumps desired_version, sets PENDING_UPDATE, and queues sync
    resp_act, wi_act, queue_act, deprov_act, wi_disp_act = await _run_case(
        WhiteInternetStatus.ACTIVE,
        WhiteInternetProvisioningStatus.ACTIVE,
    )
    assert resp_act.status == 200
    assert wi_act.provisioning_status == WhiteInternetProvisioningStatus.PENDING_UPDATE
    assert wi_act.desired_version == 6
    queue_act.assert_awaited_once()
    assert deprov_act.call_count == 1
    assert wi_disp_act.call_count == 1


@pytest.mark.asyncio
async def test_api_admin_webhooks_handler_returns_pending_and_dead_without_processed_at():
    admin_ctx = SimpleNamespace(telegram_id=1001)
    now = now_utc()
    rows = [
        SimpleNamespace(
            id=1,
            event_key="evt_1",
            provider="yookassa",
            event_type="payment.succeeded",
            provider_object_id="pay_1",
            public_order_id="ORD-1",
            payment_external_id="pay_1",
            status=WebhookInboxStatus.PENDING,
            received_at=now,
        ),
        SimpleNamespace(
            id=2,
            event_key="evt_2",
            provider="yookassa",
            event_type="refund.succeeded",
            provider_object_id="ref_2",
            public_order_id="ORD-2",
            payment_external_id="pay_2",
            status=WebhookInboxStatus.DEAD,
            received_at=now,
        ),
    ]
    session = AsyncMock()
    session.scalars = AsyncMock(return_value=SimpleNamespace(all=lambda: rows))

    with (
        patch.object(handlers_admin, "require_admin_user", return_value=(admin_ctx, None)),
        patch.object(handlers_admin, "session_scope", _make_session_scope(session)),
    ):
        resp = await handlers_admin.api_admin_webhooks_handler(SimpleNamespace())

    assert resp.status == 200
    data = json.loads(resp.text)
    assert data["ok"] is True
    assert len(data["webhooks"]) == 2
    assert [w["status"] for w in data["webhooks"]] == [
        WebhookInboxStatus.PENDING,
        WebhookInboxStatus.DEAD,
    ]
    for item in data["webhooks"]:
        assert "processed_at" not in item


@pytest.mark.asyncio
async def test_broadcast_conflict_returns_409_and_serialize_broadcast_fields():
    admin_ctx = SimpleNamespace(telegram_id=1001)
    request = SimpleNamespace(
        app={},
        json=AsyncMock(
            return_value={
                "target_audience": "all",
                "text": "Important update",
                "media_url": "https://cdn.example.com/banner.png",
            }
        ),
    )

    with (
        patch.object(handlers_admin, "require_admin_user", return_value=(admin_ctx, None)),
        patch.object(
            broadcast_service,
            "create_and_start_broadcast",
            AsyncMock(
                side_effect=BroadcastConflictError(
                    "Уже выполняется другая рассылка. Дождитесь завершения или остановите её."
                )
            ),
        ),
    ):
        resp = await handlers_admin.api_admin_broadcast_start_handler(request)

    assert resp.status == 409
    data = json.loads(resp.text)
    assert data["ok"] is False
    assert "Уже выполняется" in data["error"]

    long_text = "X" * 250
    now = now_utc()
    bp = SimpleNamespace(
        id=9,
        admin_id=1001,
        target_audience="all",
        label="Все пользователи",
        content_type="photo",
        media_id="https://cdn.example.com/banner.png",
        broadcast_text=long_text,
        status="in_progress",
        total_count=100,
        success_count=40,
        fail_count=5,
        created_at=now,
        updated_at=now,
    )
    serialized = serialize_broadcast(bp)
    assert serialized["text"] == long_text
    assert serialized["text_preview"] == "X" * 180
    assert serialized["media_url"] == "https://cdn.example.com/banner.png"
    assert serialized["has_media"] is True


@pytest.mark.asyncio
async def test_admin_nodes_lifecycle_toggle_and_delete_guards():
    session = AsyncMock()

    # 1. set_node_lifecycle rejects DECOMMISSIONING for non-ru_hub
    exit_node = SimpleNamespace(
        id=10,
        name="Exit EE",
        role="exit_node",
        capabilities=["exit_node"],
        is_active=True,
        health_state=ServerHealthState.ONLINE,
        lifecycle_status=ServerLifecycleStatus.ACTIVE,
        extra_data={},
    )
    with (
        patch.object(admin_nodes_service, "session_scope", _make_session_scope(session)),
        patch.object(admin_nodes_service, "get_server_by_id", AsyncMock(return_value=exit_node)),
    ):
        with pytest.raises(AdminNodeServiceError) as exc_info:
            await admin_nodes_service.set_node_lifecycle(10, "DECOMMISSIONING", 1001)
    assert exc_info.value.status_code == 400

    # 2a. set_node_lifecycle rejects ACTIVE on plain ACTIVE + MANUAL_DISABLED node
    manual_disabled_active_hub = SimpleNamespace(
        id=11,
        name="Disabled Hub",
        role="ru_hub",
        capabilities=["ru_hub"],
        is_active=False,
        health_state=ServerHealthState.MANUAL_DISABLED,
        lifecycle_status=ServerLifecycleStatus.ACTIVE,
        extra_data={},
    )
    with (
        patch.object(admin_nodes_service, "session_scope", _make_session_scope(session)),
        patch.object(
            admin_nodes_service,
            "get_server_by_id",
            AsyncMock(return_value=manual_disabled_active_hub),
        ),
    ):
        with pytest.raises(AdminNodeServiceError) as exc_info:
            await admin_nodes_service.set_node_lifecycle(11, "ACTIVE", 1001)
    assert exc_info.value.status_code == 400

    # 2b. DECOMMISSIONING + MANUAL_DISABLED can return to ACTIVE without re-enabling is_active
    decomm_disabled_hub = SimpleNamespace(
        id=14,
        name="Decomm Disabled Hub",
        role="ru_hub",
        capabilities=["ru_hub"],
        is_active=False,
        disabled_reason="manual",
        disabled_at=None,
        health_state=ServerHealthState.MANUAL_DISABLED,
        lifecycle_status=ServerLifecycleStatus.DECOMMISSIONING,
        extra_data={},
        country_flag="🇷🇺",
        protocol="xray",
        api_url="https://hub14.example",
        api_key="secret",
        consecutive_fails=0,
        problem_started_at=None,
        xray_instance_epoch=None,
        last_successful_check=None,
        created_at=None,
    )

    async def _fake_update_server(_sess, srv, **kwargs):
        for k, v in kwargs.items():
            setattr(srv, k, v)
        return srv

    with (
        patch.object(admin_nodes_service, "session_scope", _make_session_scope(session)),
        patch.object(
            admin_nodes_service,
            "get_server_by_id",
            AsyncMock(return_value=decomm_disabled_hub),
        ),
        patch.object(admin_nodes_service, "update_server", AsyncMock(side_effect=_fake_update_server)),
        patch.object(admin_nodes_service, "_queue_topology_sync", MagicMock()),
        patch.object(admin_nodes_service.AuditService, "log_action", AsyncMock()),
    ):
        recovered = await admin_nodes_service.set_node_lifecycle(14, "ACTIVE", 1001)
    assert recovered["lifecycle_status"] == ServerLifecycleStatus.ACTIVE.value
    assert decomm_disabled_hub.lifecycle_status == ServerLifecycleStatus.ACTIVE.value
    assert decomm_disabled_hub.is_active is False
    assert decomm_disabled_hub.health_state == ServerHealthState.MANUAL_DISABLED

    # 3. toggle_node rejects activating a DECOMMISSIONING node
    decomm_hub = SimpleNamespace(
        id=12,
        name="Drain Hub",
        role="ru_hub",
        capabilities=["ru_hub"],
        is_active=False,
        health_state=ServerHealthState.MANUAL_DISABLED,
        lifecycle_status=ServerLifecycleStatus.DECOMMISSIONING,
        extra_data={},
    )
    with patch.object(admin_nodes_service, "get_server_by_id", AsyncMock(return_value=decomm_hub)):
        with pytest.raises(AdminNodeServiceError) as exc_info:
            await admin_nodes_service.toggle_node(session, admin_id=1001, server_id=12, is_active=True)
    assert exc_info.value.status_code == 400

    # 4. delete_node(..., force=False) raises 409 on active node
    active_hub = SimpleNamespace(
        id=13,
        name="Active Hub",
        role="ru_hub",
        capabilities=["ru_hub"],
        is_active=True,
        health_state=ServerHealthState.ONLINE,
        lifecycle_status=ServerLifecycleStatus.ACTIVE,
        extra_data={},
    )
    with (
        patch.object(admin_nodes_service, "get_server_by_id", AsyncMock(return_value=active_hub)),
        patch.object(admin_nodes_service, "get_all_servers", AsyncMock(return_value=[active_hub])),
    ):
        with pytest.raises(AdminNodeServiceError) as exc_info:
            await admin_nodes_service.delete_node(session, admin_id=1001, server_id=13, force=False)
    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_api_admin_user_restrictions_wi_reactivation_before_held_orders_and_exhausted_recovery():
    admin_ctx = SimpleNamespace(telegram_id=1001)
    now = now_utc()

    # Case 0: Enabling financial_hold persists financial_block_reason from note
    user_0 = SimpleNamespace(
        id=50,
        telegram_id=5000,
        is_deleted=False,
        financial_hold=False,
        topup_blocked=False,
        financial_block_reason=None,
    )
    session_0 = AsyncMock()
    session_0.get = AsyncMock(return_value=user_0)
    session_0.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: user_0))
    req_0 = SimpleNamespace(
        match_info={"user_id": "50"},
        json=AsyncMock(return_value={"action": "restrictions", "financial_hold": True, "note": "chargeback #42"}),
    )
    with (
        patch.object(handlers_admin, "require_admin_user", return_value=(admin_ctx, None)),
        patch.object(handlers_admin, "session_scope", _make_session_scope(session_0)),
        patch.object(handlers_admin, "acquire_user_account_lock", AsyncMock()),
        patch.object(handlers_admin.SubscriptionService, "sync_access_state", AsyncMock()),
        patch.object(handlers_admin.AuditService, "log_action", AsyncMock()),
    ):
        resp_0 = await handlers_admin.api_admin_user_adjust_handler(req_0)
    assert resp_0.status == 200
    assert user_0.financial_hold is True
    assert user_0.financial_block_reason == "chargeback #42"

    # Case 1a: Held wallet topup + held WI purchase settle BEFORE manual WI reactivation (purchase sees DISABLED)
    user_1a = SimpleNamespace(
        id=51,
        telegram_id=5100,
        is_deleted=False,
        financial_hold=True,
        topup_blocked=False,
        financial_block_reason="dispute",
    )
    wi_1a = SimpleNamespace(
        id=501,
        user_id=51,
        status=WhiteInternetStatus.DISABLED,
        status_reason="financial_hold",
        expires_at=now + timedelta(days=10),
        base_traffic_bytes=50 * 1024**3,
        extra_traffic_bytes=0,
        traffic_used_bytes=1024,
        traffic_overage_bytes=0,
        desired_version=2,
        provisioning_status=WhiteInternetProvisioningStatus.SYNCED_INACTIVE,
    )
    held_topup_wallet = SimpleNamespace(id="ord-topup-1", service_type="topup", metadata_={"operation": "topup"})
    held_wi_purchase = SimpleNamespace(id="ord-purchase-1", service_type="white_internet", metadata_={"operation": "purchase"})

    session_1a = AsyncMock()
    session_1a.get = AsyncMock(return_value=user_1a)
    session_1a.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: user_1a))
    session_1a.scalar = AsyncMock(return_value=None)
    session_1a.scalars = AsyncMock(return_value=SimpleNamespace(all=lambda: [held_wi_purchase, held_topup_wallet]))

    release_log_1a: list[tuple[str, str]] = []

    async def _fake_mark_paid_1a(_sess, order_id):
        release_log_1a.append((str(order_id), str(wi_1a.status)))
        if order_id == "ord-purchase-1":
            wi_1a.status = WhiteInternetStatus.PENDING
        return held_wi_purchase

    queue_wi_1a = AsyncMock()
    req_1a = SimpleNamespace(
        match_info={"user_id": "51"},
        json=AsyncMock(return_value={"action": "restrictions", "financial_hold": False}),
    )

    with (
        patch.object(handlers_admin, "require_admin_user", return_value=(admin_ctx, None)),
        patch.object(handlers_admin, "session_scope", _make_session_scope(session_1a)),
        patch.object(handlers_admin, "acquire_user_account_lock", AsyncMock()),
        patch.object(handlers_admin.SubscriptionService, "sync_access_state", AsyncMock()),
        patch.object(handlers_admin, "get_subscription_by_user_id", AsyncMock(return_value=wi_1a)),
        patch.object(
            handlers_admin.white_internet_repo,
            "get_subscription_with_lock",
            AsyncMock(return_value=wi_1a),
        ),
        patch.object(handlers_admin, "_queue_wi_inline_sync", queue_wi_1a),
        patch.object(handlers_admin.OrderService, "mark_order_paid", AsyncMock(side_effect=_fake_mark_paid_1a)),
        patch.object(handlers_admin.AuditService, "log_action", AsyncMock()),
    ):
        resp_1a = await handlers_admin.api_admin_user_adjust_handler(req_1a)

    assert resp_1a.status == 200
    data_1a = json.loads(resp_1a.text)
    assert data_1a["ok"] is True
    assert data_1a["wi_reactivated"] is False
    assert release_log_1a == [
        ("ord-topup-1", WhiteInternetStatus.DISABLED.value),
        ("ord-purchase-1", WhiteInternetStatus.DISABLED.value),
    ]
    assert wi_1a.status == WhiteInternetStatus.PENDING
    assert user_1a.financial_block_reason is None
    queue_wi_1a.assert_not_awaited()

    # Case 1b: DISABLED WI sub from hold + held WI renew/topup reactivates WI to ACTIVE BEFORE settling renew/topup
    user_1 = SimpleNamespace(
        id=53,
        telegram_id=5300,
        is_deleted=False,
        financial_hold=True,
        topup_blocked=False,
        financial_block_reason="dispute",
    )
    wi_1 = SimpleNamespace(
        id=503,
        user_id=53,
        status=WhiteInternetStatus.DISABLED,
        status_reason="financial_hold",
        expires_at=now + timedelta(days=10),
        base_traffic_bytes=50 * 1024**3,
        extra_traffic_bytes=0,
        traffic_used_bytes=1024,
        traffic_overage_bytes=0,
        desired_version=2,
        provisioning_status=WhiteInternetProvisioningStatus.SYNCED_INACTIVE,
    )
    held_renew = SimpleNamespace(id="ord-held-renew", service_type="white_internet", metadata_={"operation": "renew"})
    held_wi_topup = SimpleNamespace(id="ord-held-topup", service_type="white_internet", metadata_={"operation": "topup"})

    session_1 = AsyncMock()
    session_1.get = AsyncMock(return_value=user_1)
    session_1.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: user_1))
    session_1.scalar = AsyncMock(return_value=None)
    session_1.scalars = AsyncMock(return_value=SimpleNamespace(all=lambda: [held_renew, held_wi_topup]))

    status_at_order_release: list[tuple[str, str]] = []

    async def _fake_mark_paid(_sess, order_id):
        status_at_order_release.append((str(order_id), str(wi_1.status)))
        return held_renew

    queue_wi_1 = AsyncMock()
    req_1 = SimpleNamespace(
        match_info={"user_id": "53"},
        json=AsyncMock(return_value={"action": "restrictions", "financial_hold": False}),
    )

    with (
        patch.object(handlers_admin, "require_admin_user", return_value=(admin_ctx, None)),
        patch.object(handlers_admin, "session_scope", _make_session_scope(session_1)),
        patch.object(handlers_admin, "acquire_user_account_lock", AsyncMock()),
        patch.object(handlers_admin.SubscriptionService, "sync_access_state", AsyncMock()),
        patch.object(handlers_admin, "get_subscription_by_user_id", AsyncMock(return_value=wi_1)),
        patch.object(
            handlers_admin.white_internet_repo,
            "get_subscription_with_lock",
            AsyncMock(return_value=wi_1),
        ),
        patch.object(handlers_admin, "_queue_wi_inline_sync", queue_wi_1),
        patch.object(handlers_admin.OrderService, "mark_order_paid", AsyncMock(side_effect=_fake_mark_paid)),
        patch.object(handlers_admin.AuditService, "log_action", AsyncMock()),
    ):
        resp_1 = await handlers_admin.api_admin_user_adjust_handler(req_1)

    assert resp_1.status == 200
    data_1 = json.loads(resp_1.text)
    assert data_1["ok"] is True
    assert data_1["wi_reactivated"] is True
    assert status_at_order_release == [
        ("ord-held-renew", WhiteInternetStatus.ACTIVE.value),
        ("ord-held-topup", WhiteInternetStatus.ACTIVE.value),
    ]
    assert wi_1.status == WhiteInternetStatus.ACTIVE
    assert wi_1.status_reason is None
    assert wi_1.provisioning_status == WhiteInternetProvisioningStatus.PENDING_UPDATE
    assert wi_1.desired_version == 3
    queue_wi_1.assert_awaited_once()

    # Case 2: EXHAUSTED-before-hold subscription transitions DISABLED -> EXHAUSTED (not stuck DISABLED)
    user_2 = SimpleNamespace(
        id=52,
        telegram_id=5200,
        is_deleted=False,
        financial_hold=True,
        topup_blocked=False,
        financial_block_reason="dispute",
    )
    wi_2 = SimpleNamespace(
        id=502,
        user_id=52,
        status=WhiteInternetStatus.DISABLED,
        status_reason="financial_hold",
        expires_at=now + timedelta(days=10),
        base_traffic_bytes=50 * 1024**3,
        extra_traffic_bytes=0,
        traffic_used_bytes=50 * 1024**3,
        traffic_overage_bytes=0,
        desired_version=4,
        provisioning_status=WhiteInternetProvisioningStatus.PENDING_DELETE,
    )
    session_2 = AsyncMock()
    session_2.get = AsyncMock(return_value=user_2)
    session_2.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: user_2))
    session_2.scalar = AsyncMock(return_value=None)
    session_2.scalars = AsyncMock(return_value=SimpleNamespace(all=lambda: []))

    queue_wi_2 = AsyncMock()
    req_2 = SimpleNamespace(
        match_info={"user_id": "52"},
        json=AsyncMock(return_value={"action": "restrictions", "financial_hold": False}),
    )

    with (
        patch.object(handlers_admin, "require_admin_user", return_value=(admin_ctx, None)),
        patch.object(handlers_admin, "session_scope", _make_session_scope(session_2)),
        patch.object(handlers_admin, "acquire_user_account_lock", AsyncMock()),
        patch.object(handlers_admin.SubscriptionService, "sync_access_state", AsyncMock()),
        patch.object(handlers_admin, "get_subscription_by_user_id", AsyncMock(return_value=wi_2)),
        patch.object(
            handlers_admin.white_internet_repo,
            "get_subscription_with_lock",
            AsyncMock(return_value=wi_2),
        ),
        patch.object(handlers_admin, "_queue_wi_inline_sync", queue_wi_2),
        patch.object(handlers_admin.AuditService, "log_action", AsyncMock()),
    ):
        resp_2 = await handlers_admin.api_admin_user_adjust_handler(req_2)

    assert resp_2.status == 200
    data_2 = json.loads(resp_2.text)
    assert data_2["ok"] is True
    assert data_2["wi_reactivated"] is False
    assert wi_2.status == WhiteInternetStatus.EXHAUSTED
    assert wi_2.status_reason == "quota_exhausted"
    assert wi_2.provisioning_status == WhiteInternetProvisioningStatus.SYNCED_INACTIVE
    queue_wi_2.assert_not_awaited()


@pytest.mark.asyncio
async def test_api_admin_user_grant_wi_guards_financial_hold_and_disabled_sub():
    admin_ctx = SimpleNamespace(telegram_id=1001)

    # Case 1: financial_hold blocks grant_wi with 400
    held_user = SimpleNamespace(id=61, telegram_id=6100, is_deleted=False, financial_hold=True)
    session_1 = AsyncMock()
    session_1.get = AsyncMock(return_value=held_user)
    session_1.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: held_user))
    req_1 = SimpleNamespace(
        match_info={"user_id": "61"},
        json=AsyncMock(return_value={"action": "grant_wi", "days": 30, "device_limit": 1}),
    )

    with (
        patch.object(handlers_admin, "require_admin_user", return_value=(admin_ctx, None)),
        patch.object(handlers_admin, "session_scope", _make_session_scope(session_1)),
        patch.object(handlers_admin, "acquire_user_account_lock", AsyncMock()),
    ):
        resp_1 = await handlers_admin.api_admin_user_adjust_handler(req_1)

    assert resp_1.status == 400
    data_1 = json.loads(resp_1.text)
    assert data_1["ok"] is False
    assert "финансовая блокировка" in data_1["error"]

    # Case 2: DISABLED wi_sub with days <= 0 (0 or negative) rejects with 400 without mutating status or limit
    free_user = SimpleNamespace(id=62, telegram_id=6200, is_deleted=False, financial_hold=False)
    for bad_payload in (
        {"action": "grant_wi", "days": 0, "device_limit": 2},
        {"action": "grant_wi", "days": -5},
    ):
        wi_disabled = SimpleNamespace(
            id=602,
            user_id=62,
            status=WhiteInternetStatus.DISABLED,
            device_limit=1,
            expires_at=now_utc() + timedelta(days=5),
        )
        session_2 = AsyncMock()
        session_2.get = AsyncMock(return_value=free_user)
        session_2.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: free_user))
        req_2 = SimpleNamespace(
            match_info={"user_id": "62"},
            json=AsyncMock(return_value=bad_payload),
        )
        with (
            patch.object(handlers_admin, "require_admin_user", return_value=(admin_ctx, None)),
            patch.object(handlers_admin, "session_scope", _make_session_scope(session_2)),
            patch.object(handlers_admin, "acquire_user_account_lock", AsyncMock()),
            patch.object(handlers_admin, "get_subscription_by_user_id", AsyncMock(return_value=wi_disabled)),
        ):
            resp_2 = await handlers_admin.api_admin_user_adjust_handler(req_2)

        assert resp_2.status == 400
        data_2 = json.loads(resp_2.text)
        assert data_2["ok"] is False
        assert "отключена" in data_2["error"]
        assert wi_disabled.status == WhiteInternetStatus.DISABLED
        assert wi_disabled.device_limit == 1


@pytest.mark.asyncio
async def test_admin_ledger_debt_forgiveness_and_token_rotate_expired_wi():
    now = now_utc()
    entry_with_note = SimpleNamespace(
        id=1,
        entry_type=AccountLedgerEntryType.PAYMENT_CREDIT,
        bucket="real",
        amount=Decimal("300.00"),
        metadata_={"source": "admin_debt_forgiveness", "note": "тикет #12"},
        created_at=now,
    )
    entry_no_note = SimpleNamespace(
        id=2,
        entry_type=AccountLedgerEntryType.PAYMENT_CREDIT,
        bucket="real",
        amount=Decimal("100.00"),
        metadata_={"source": "admin_debt_forgiveness"},
        created_at=now,
    )
    ser_1 = handlers_admin._serialize_admin_ledger_entry(entry_with_note)
    ser_2 = handlers_admin._serialize_admin_ledger_entry(entry_no_note)
    assert ser_1["entry_type"] == "debt_forgiveness"
    assert ser_1["description"] == "Погашение долга · тикет #12"
    assert ser_2["entry_type"] == "debt_forgiveness"
    assert ser_2["description"] == "Погашение долга"

    # token_rotate on EXPIRED WI subscription must not bump desired_version in handler and revokes via WI hubs
    admin_ctx = SimpleNamespace(telegram_id=1001)
    user = SimpleNamespace(id=71, telegram_id=7100, is_deleted=False)
    wi_exp = SimpleNamespace(
        id=701,
        user_id=71,
        uuid="old-wi-uuid",
        status=WhiteInternetStatus.EXPIRED,
        desired_version=5,
        provisioning_status=WhiteInternetProvisioningStatus.SYNCED_INACTIVE,
    )
    wi_hub = SimpleNamespace(id=88, api_url="https://wi-hub.example", api_key="k", protocol="xray")
    session = AsyncMock()
    session.scalar = AsyncMock(return_value=user)
    session.scalars = AsyncMock(return_value=SimpleNamespace(all=lambda: []))
    wi_dispatch_mock = MagicMock()
    queue_wi_mock = AsyncMock()
    req = SimpleNamespace(app={}, match_info={"user_id": "71"}, text=AsyncMock(return_value=""))

    with (
        patch.object(handlers_admin, "require_admin_user", return_value=(admin_ctx, None)),
        patch.object(handlers_admin, "session_scope", _make_session_scope(session)),
        patch.object(handlers_admin, "acquire_user_account_lock", AsyncMock()),
        patch.object(handlers_admin.vless_subscription_repo, "get_subscription_by_user_id", AsyncMock(return_value=None)),
        patch.object(handlers_admin, "get_subscription_by_user_id", AsyncMock(return_value=wi_exp)),
        patch.object(handlers_admin.white_internet_repo, "reset_subscription_token_atomic", AsyncMock(return_value="tok123")),
        patch.object(handlers_admin.white_internet_repo, "get_subscription_with_lock", AsyncMock(return_value=wi_exp)),
        patch.object(handlers_admin.white_internet_repo, "add_pending_revocation_uuids", AsyncMock()),
        patch.object(handlers_admin.servers_repo, "get_wi_revocation_ru_hubs", AsyncMock(return_value=[wi_hub])),
        patch.object(white_internet_service, "_dispatch_deprovision", wi_dispatch_mock),
        patch.object(handlers_admin, "_queue_wi_inline_sync", queue_wi_mock),
        patch.object(handlers_admin, "_build_sub_url", return_value="https://sub.example/tok123"),
        patch.object(handlers_admin, "_build_backup_sub_url", return_value="https://backup.example/tok123"),
        patch.object(handlers_admin.AuditService, "log_action", AsyncMock()),
    ):
        resp = await handlers_admin.api_admin_user_token_rotate_handler(req)

    assert resp.status == 200
    assert wi_exp.desired_version == 5
    assert wi_exp.provisioning_status == WhiteInternetProvisioningStatus.SYNCED_INACTIVE
    wi_dispatch_mock.assert_called_once()
    queue_wi_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_wi_devices_reset_exhausted_bumps_version_and_token_rotate_active_no_double_bump():
    admin_ctx = SimpleNamespace(telegram_id=1001)
    user = SimpleNamespace(id=72, telegram_id=7200, is_deleted=False)
    wi_hub = SimpleNamespace(id=89, api_url="https://wi-hub.example", api_key="k", protocol="xray")

    # 1. devices_reset on EXHAUSTED WI bumps desired_version (5 -> 6) without switching to PENDING_UPDATE or inline sync
    wi_exh = SimpleNamespace(
        id=702,
        user_id=72,
        uuid="wi-uuid-exh",
        status=WhiteInternetStatus.EXHAUSTED,
        desired_version=5,
        provisioning_status=WhiteInternetProvisioningStatus.SYNCED_INACTIVE,
    )
    devs = [SimpleNamespace(device_uuid="dev-uuid-exh-1")]
    session_reset = AsyncMock()
    session_reset.scalar = AsyncMock(return_value=user)
    session_reset.scalars = AsyncMock(return_value=SimpleNamespace(all=lambda: devs))
    session_reset.execute = AsyncMock(return_value=SimpleNamespace(rowcount=1))
    wi_dispatch_reset = MagicMock()
    queue_wi_reset = AsyncMock()
    req_reset = SimpleNamespace(app={}, match_info={"user_id": "72"}, text=AsyncMock(return_value=""))

    with (
        patch.object(handlers_admin, "require_admin_user", return_value=(admin_ctx, None)),
        patch.object(handlers_admin, "session_scope", _make_session_scope(session_reset)),
        patch.object(handlers_admin, "acquire_user_account_lock", AsyncMock()),
        patch.object(handlers_admin.vless_subscription_repo, "get_subscription_by_user_id", AsyncMock(return_value=None)),
        patch.object(handlers_admin, "get_subscription_by_user_id", AsyncMock(return_value=wi_exh)),
        patch.object(handlers_admin.white_internet_repo, "get_subscription_with_lock", AsyncMock(return_value=wi_exh)),
        patch.object(handlers_admin.white_internet_repo, "add_pending_revocation_uuids", AsyncMock()),
        patch.object(handlers_admin.servers_repo, "get_wi_revocation_ru_hubs", AsyncMock(return_value=[wi_hub])),
        patch.object(white_internet_service, "_dispatch_deprovision", wi_dispatch_reset),
        patch.object(handlers_admin, "_queue_wi_inline_sync", queue_wi_reset),
        patch.object(handlers_admin.AuditService, "log_action", AsyncMock()),
    ):
        resp_reset = await handlers_admin.api_admin_user_devices_reset_handler(req_reset)

    assert resp_reset.status == 200
    assert wi_exh.desired_version == 6
    assert wi_exh.provisioning_status == WhiteInternetProvisioningStatus.SYNCED_INACTIVE
    wi_dispatch_reset.assert_called_once()
    assert wi_dispatch_reset.call_args.args[2] == 6
    queue_wi_reset.assert_not_awaited()

    # 2. token_rotate on ACTIVE WI does not double-increment desired_version after reset_subscription_token_atomic
    wi_act = SimpleNamespace(
        id=703,
        user_id=72,
        uuid="wi-uuid-old",
        status=WhiteInternetStatus.ACTIVE,
        desired_version=7,
        provisioning_status=WhiteInternetProvisioningStatus.ACTIVE,
    )

    async def _fake_reset_token(_sess, _sub_id, **_kw):
        wi_act.desired_version += 1
        wi_act.uuid = "wi-uuid-new"
        wi_act.provisioning_status = WhiteInternetProvisioningStatus.PENDING_UPDATE
        return "tok-new"

    session_rot = AsyncMock()
    session_rot.scalar = AsyncMock(return_value=user)
    session_rot.scalars = AsyncMock(return_value=SimpleNamespace(all=lambda: []))
    wi_dispatch_rot = MagicMock()
    queue_wi_rot = AsyncMock()
    req_rot = SimpleNamespace(app={}, match_info={"user_id": "72"}, text=AsyncMock(return_value=""))

    with (
        patch.object(handlers_admin, "require_admin_user", return_value=(admin_ctx, None)),
        patch.object(handlers_admin, "session_scope", _make_session_scope(session_rot)),
        patch.object(handlers_admin, "acquire_user_account_lock", AsyncMock()),
        patch.object(handlers_admin.vless_subscription_repo, "get_subscription_by_user_id", AsyncMock(return_value=None)),
        patch.object(handlers_admin, "get_subscription_by_user_id", AsyncMock(return_value=wi_act)),
        patch.object(handlers_admin.white_internet_repo, "reset_subscription_token_atomic", AsyncMock(side_effect=_fake_reset_token)),
        patch.object(handlers_admin.white_internet_repo, "get_subscription_with_lock", AsyncMock(return_value=wi_act)),
        patch.object(handlers_admin.white_internet_repo, "add_pending_revocation_uuids", AsyncMock()),
        patch.object(handlers_admin.servers_repo, "get_wi_revocation_ru_hubs", AsyncMock(return_value=[wi_hub])),
        patch.object(white_internet_service, "_dispatch_deprovision", wi_dispatch_rot),
        patch.object(handlers_admin, "_queue_wi_inline_sync", queue_wi_rot),
        patch.object(handlers_admin, "_build_sub_url", return_value="https://sub.example/tok-new"),
        patch.object(handlers_admin, "_build_backup_sub_url", return_value="https://backup.example/tok-new"),
        patch.object(handlers_admin.AuditService, "log_action", AsyncMock()),
    ):
        resp_rot = await handlers_admin.api_admin_user_token_rotate_handler(req_rot)

    assert resp_rot.status == 200
    assert wi_act.desired_version == 8
    assert wi_act.provisioning_status == WhiteInternetProvisioningStatus.PENDING_UPDATE
    wi_dispatch_rot.assert_called_once()
    assert wi_dispatch_rot.call_args.args[2] == 8
    queue_wi_rot.assert_awaited_once()


@pytest.mark.asyncio
async def test_api_admin_pagination_offset_clamping():
    admin_ctx = SimpleNamespace(telegram_id=1001)
    huge_offset = "999999999999999999999"

    # 1. users
    sess_users = AsyncMock()
    sess_users.scalar = AsyncMock(side_effect=[Decimal("0"), 0])
    sess_users.scalars = AsyncMock(return_value=SimpleNamespace(all=lambda: []))
    req_users = SimpleNamespace(query={"offset": huge_offset, "limit": "10"})
    with (
        patch.object(handlers_admin, "require_admin_user", return_value=(admin_ctx, None)),
        patch.object(handlers_admin, "session_scope", _make_session_scope(sess_users)),
        patch.object(handlers_admin.users_repo, "get_user_filter_counts", AsyncMock(return_value={"all": 0})),
    ):
        resp_users = await handlers_admin.api_admin_users_handler(req_users)
    assert resp_users.status == 200
    assert json.loads(resp_users.text)["offset"] == 2_147_483_647

    # 2. broadcasts
    sess_bc = AsyncMock()
    req_bc = SimpleNamespace(query={"offset": huge_offset, "limit": "15"})
    list_bc_mock = AsyncMock(return_value=([], 0))
    with (
        patch.object(handlers_admin, "require_admin_user", return_value=(admin_ctx, None)),
        patch.object(handlers_admin, "session_scope", _make_session_scope(sess_bc)),
        patch.object(handlers_admin.broadcast_service, "list_broadcasts", list_bc_mock),
    ):
        resp_bc = await handlers_admin.api_admin_broadcasts_list_handler(req_bc)
    assert resp_bc.status == 200
    assert json.loads(resp_bc.text)["offset"] == 2_147_483_647
    assert list_bc_mock.call_args.kwargs["offset"] == 2_147_483_647

    # 3. finances & 4. audit-logs
    sess_tools = AsyncMock()
    fin_mock = AsyncMock(return_value={"total": 0, "offset": 2_147_483_647, "limit": 25, "items": []})
    audit_mock = AsyncMock(return_value={"total": 0, "offset": 0, "limit": 40, "actions": [], "items": []})
    with (
        patch.object(handlers_admin, "require_admin_user", return_value=(admin_ctx, None)),
        patch.object(handlers_admin, "session_scope", _make_session_scope(sess_tools)),
        patch.object(handlers_admin.admin_tools_service, "list_finances", fin_mock),
        patch.object(handlers_admin.admin_tools_service, "list_audit_logs", audit_mock),
    ):
        resp_fin = await handlers_admin.api_admin_finances_handler(
            SimpleNamespace(query={"offset": huge_offset})
        )
        resp_aud = await handlers_admin.api_admin_audit_logs_handler(
            SimpleNamespace(query={"offset": "-50"})
        )
    assert resp_fin.status == 200
    assert fin_mock.call_args.kwargs["offset"] == 2_147_483_647
    assert resp_aud.status == 200
    assert audit_mock.call_args.kwargs["offset"] == 0


@pytest.mark.asyncio
async def test_node_monitor_nonblocking_topology_and_health_fields():
    from services.workers import node_monitor

    existing = {
        "role": "ru_hub",
        "ingress_problem": True,
        "hosting_alert_level": 80,
        "module_errors": {"xray": "old"},
    }
    health_data = {
        "disk_usage": {"used_percent": 41.2, "free_gb": 22.5},
        "module_errors": {},
        "traffic_limit": {"used_bytes": 1000, "limit_bytes": 5000, "exceeded": False},
        "system_load": {"cpu_percent": 12.0, "ram_percent": 33.0},
    }
    patch_extra = node_monitor._build_v2_extra_patch(existing, None, health_data)
    assert patch_extra["disk_usage"] == {"used_percent": 41.2, "free_gb": 22.5}
    assert patch_extra["module_errors"] == {}
    assert patch_extra["traffic_limit"] == {"used_bytes": 1000, "limit_bytes": 5000, "exceeded": False}
    assert patch_extra["system_load"] == {"cpu_percent": 12.0, "ram_percent": 33.0}
    assert "ingress_problem" not in patch_extra
    assert "hosting_alert_level" not in patch_extra
    assert "role" not in patch_extra

    # Verify check_node_resources_and_alerts triggers topology sync on transitions to/from PROBLEM,
    # and does NOT trigger on ONLINE -> WAITING_CONFIRMATION.
    def _make_hub(health_state: ServerHealthState, consecutive_fails: int = 0, consecutive_successes: int = 0):
        return SimpleNamespace(
            id=1,
            name="Hub 1",
            role="ru_hub",
            protocol="xray",
            api_url="https://hub1.example",
            api_key="key",
            is_active=True,
            health_state=health_state,
            consecutive_fails=consecutive_fails,
            consecutive_successes=consecutive_successes,
            last_alert_sent_state=None,
            problem_started_at=None,
            next_check_at=None,
            disabled_reason=None,
            last_successful_check=None,
            capabilities=["ru_hub"],
            extra_data={"role": "ru_hub"},
            xray_instance_epoch="ep1",
            xray_instance_boot_id="b1",
            xray_instance_starttime=100,
        )

    @asynccontextmanager
    async def _run_monitor_tick(srv_obj, check_health_result):
        sess = AsyncMock()
        nonblocking_mock = AsyncMock(return_value={1: True})
        fake_xray_client = SimpleNamespace(
            check_health=AsyncMock(return_value=check_health_result),
        )

        @asynccontextmanager
        async def _xray_ctx(*_a, **_kw):
            yield fake_xray_client

        with (
            patch.object(node_monitor, "session_scope", _make_session_scope(sess)),
            patch.object(node_monitor, "get_all_servers", AsyncMock(return_value=[srv_obj])),
            patch("services.xray_node_client.XrayNodeClient", _xray_ctx),
            patch.object(
                node_monitor,
                "update_server_health_snapshot",
                AsyncMock(return_value=(srv_obj, True)),
            ),
            patch.object(node_monitor, "_send_admin_alert_msg", AsyncMock(return_value=False)),
            patch("services.topology_sync_service.sync_v2_topology_to_nodes_nonblocking", nonblocking_mock),
        ):
            await node_monitor.check_node_resources_and_alerts(bot=None)
            yield nonblocking_mock

    # 1. ONLINE -> WAITING_CONFIRMATION must NOT trigger topology sync
    node_monitor._server_states.clear()
    srv_online = _make_hub(ServerHealthState.ONLINE, consecutive_fails=0)
    async with _run_monitor_tick(srv_online, (False, None, None)) as nb_mock:
        nb_mock.assert_not_awaited()
    assert node_monitor._server_states[1].health_state == ServerHealthState.WAITING_CONFIRMATION

    # 2. WAITING_CONFIRMATION -> PROBLEM MUST trigger topology sync
    node_monitor._server_states[1].next_check_at = None
    async with _run_monitor_tick(srv_online, (False, None, None)) as nb_mock:
        nb_mock.assert_awaited_once()
    assert node_monitor._server_states[1].health_state == ServerHealthState.PROBLEM

    # 3. PROBLEM -> ONLINE (3rd consecutive success) MUST trigger topology sync
    node_monitor._server_states.clear()
    srv_problem = _make_hub(
        ServerHealthState.PROBLEM,
        consecutive_successes=node_monitor.REQUIRED_STABLE_SUCCESSES - 1,
    )
    async with _run_monitor_tick(srv_problem, (True, None, {})) as nb_mock:
        nb_mock.assert_awaited_once()
    assert node_monitor._server_states[1].health_state == ServerHealthState.ONLINE
    node_monitor._server_states.clear()


@pytest.mark.asyncio
async def test_wi_reconciler_excludes_decommissioning_from_active_provisioning():
    from services.workers.white_internet_reconciliation import WhiteInternetReconciliationWorker

    now = now_utc()
    hub_active = SimpleNamespace(
        id=1,
        api_url="https://hub1.example",
        api_key="k1",
        capabilities=["ru_hub", "white_internet"],
        lifecycle_status=ServerLifecycleStatus.ACTIVE,
        xray_instance_epoch="ep1",
        extra_data={"xhttp_locations": [{"inbound_tag": "just1k-wl-default"}]},
    )
    hub_decomm = SimpleNamespace(
        id=2,
        api_url="https://hub2.example",
        api_key="k2",
        capabilities=["ru_hub", "white_internet"],
        lifecycle_status=ServerLifecycleStatus.DECOMMISSIONING,
        xray_instance_epoch="ep2",
        extra_data={"xhttp_locations": [{"inbound_tag": "just1k-wl-default"}]},
    )
    sub_active = SimpleNamespace(
        id=10,
        user_id=100,
        uuid="uuid-active",
        status=WhiteInternetStatus.ACTIVE,
        expires_at=now + timedelta(days=10),
        device_limit=1,
        desired_version=2,
        actual_version=1,
        node_sync_state={},
        pending_revoked_uuids=[],
    )
    sub_disabled = SimpleNamespace(
        id=11,
        user_id=101,
        uuid="uuid-disabled",
        status=WhiteInternetStatus.DISABLED,
        expires_at=now + timedelta(days=10),
        device_limit=1,
        desired_version=3,
        actual_version=2,
        node_sync_state={},
        pending_revoked_uuids=["rev-uuid-1"],
    )

    sync_calls: list[tuple[str, str, bool]] = []

    async def _fake_sync_client(api_url, _api_key, client_uuid, *, is_active, **_kw):
        sync_calls.append((api_url, client_uuid, bool(is_active)))
        return SimpleNamespace(
            result="applied",
            error=None,
            verified_epoch="ep1" if api_url == "https://hub1.example" else "ep2",
            verified_inbounds=["just1k-wl-default"],
        )

    fake_client = SimpleNamespace(sync_client=AsyncMock(side_effect=_fake_sync_client))

    sess = AsyncMock()
    sess.execute = AsyncMock(
        side_effect=[
            SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [hub_active, hub_decomm])),
            SimpleNamespace(all=lambda: [(sub_active, False, False), (sub_disabled, False, False)]),
            SimpleNamespace(all=lambda: []),
            SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [sub_disabled])),
        ]
    )
    lock_sub_map = {10: sub_active, 11: sub_disabled}

    async def _fake_get_with_lock(_s, sub_id):
        return lock_sub_map.get(sub_id)

    worker = WhiteInternetReconciliationWorker(
        node_client=fake_client,
        session_factory=_make_session_scope(sess),
    )

    with (
        patch("services.workers.white_internet_reconciliation.flag_modified", MagicMock()),
        patch("services.workers.white_internet_reconciliation.user_devices_repo.list_user_devices", AsyncMock(return_value=[])),
        patch("services.workers.white_internet_reconciliation.white_internet_repo.get_subscription_with_lock", AsyncMock(side_effect=_fake_get_with_lock)),
        patch("services.workers.white_internet_reconciliation.white_internet_repo.pop_pending_revoked_uuid", AsyncMock()),
    ):
        synced = await worker.run_reconciliation_cycle()

    assert synced >= 1
    assert ("https://hub1.example", "uuid-active", True) in sync_calls
    assert ("https://hub2.example", "uuid-active", True) not in sync_calls
    assert ("https://hub1.example", "uuid-disabled", False) in sync_calls
    assert ("https://hub2.example", "uuid-disabled", False) in sync_calls
    assert ("https://hub1.example", "rev-uuid-1", False) in sync_calls
    assert ("https://hub2.example", "rev-uuid-1", False) in sync_calls
    assert sub_active.actual_version == 2
    assert sub_disabled.actual_version == 3


@pytest.mark.asyncio
async def test_2fa_setup_token_step_replay_and_broadcast_media_url():
    import time
    from pydantic import ValidationError
    from bot.miniapp.schemas import BroadcastStartPayload
    from services import miniapp_auth_service

    # 1. BroadcastStartPayload.media_url validation
    ok_https = BroadcastStartPayload(target_audience="all", text="hi", media_url="https://cdn.example.com/a.png")
    assert ok_https.media_url == "https://cdn.example.com/a.png"
    ok_tg = BroadcastStartPayload(target_audience="all", text="hi", media_url="AgACAgIAAxkBAAIBB2c1234567890abcdef")
    assert ok_tg.media_url == "AgACAgIAAxkBAAIBB2c1234567890abcdef"
    for bad_url in (
        "http://cdn.example.com/a.png",
        "ftp://files.example.com/a.png",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "data:image/png;base64,AAAA",
    ):
        with pytest.raises(ValidationError):
            BroadcastStartPayload(target_audience="all", text="hi", media_url=bad_url)

    # 2. 2FA setup_token step replay guard
    admin_id = 987654
    secret = miniapp_auth_service.generate_totp_secret()
    step = int(time.time()) // 30
    code = miniapp_auth_service._compute_hotp(secret, step)

    miniapp_auth_service._used_totp_steps.pop((admin_id, step), None)
    miniapp_auth_service._2fa_failures.pop(admin_id, None)
    set_mock = AsyncMock()
    sess = AsyncMock()

    with (
        patch.object(miniapp_auth_service, "is_admin", return_value=True),
        patch.object(miniapp_auth_service, "_derive_signing_key", return_value=b"test-signing-key-for-totp-setup!"),
        patch.object(miniapp_auth_service, "get_admin_2fa_config", AsyncMock(return_value={"configured": False, "mode": "unconfigured"})),
        patch.object(miniapp_auth_service, "_encrypt_totp_secret", return_value="enc-secret"),
        patch.object(miniapp_auth_service, "set_system_setting", set_mock),
        patch.object(miniapp_auth_service, "get_admin_session_epoch", AsyncMock(return_value="1")),
        patch.object(miniapp_auth_service, "issue_admin_session_token", return_value=("tok", 9999999999)),
    ):
        setup_token = miniapp_auth_service.issue_totp_setup_token(admin_id, secret)
        res_first = await miniapp_auth_service.verify_admin_2fa(sess, admin_id, code, setup_token=setup_token)
        res_replay = await miniapp_auth_service.verify_admin_2fa(sess, admin_id, code, setup_token=setup_token)

    assert res_first["verified"] is True
    assert set_mock.await_count == 1
    assert res_replay["verified"] is False
    assert "использован" in res_replay["error"]
    miniapp_auth_service._used_totp_steps.pop((admin_id, step), None)


def test_db_models_targeted_indexes_present():
    from database.models import AuditLog, Order, User, UserDevice, WebhookInbox

    assert "ix_users_first_name_trgm" in {i.name for i in User.__table__.indexes}
    assert "ix_user_devices_hwid_trgm" in {i.name for i in UserDevice.__table__.indexes}
    assert {"ix_orders_attention_flag", "ix_orders_user_settlement_held"}.issubset(
        {i.name for i in Order.__table__.indexes}
    )
    assert "ix_webhook_inbox_attention_recent" in {i.name for i in WebhookInbox.__table__.indexes}
    assert {
        "ix_audit_logs_admin_id_id_desc",
        "ix_audit_logs_action_id_desc",
        "ix_audit_logs_details_trgm",
    }.issubset({i.name for i in AuditLog.__table__.indexes})


@pytest.mark.asyncio
async def test_b1_cancel_admin_background_tasks():
    import asyncio

    started = asyncio.Event()
    cancelled = False

    async def _worker():
        nonlocal cancelled
        started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled = True
            raise

    task = asyncio.create_task(_worker())
    await started.wait()
    handlers_admin._background_tasks.clear()
    handlers_admin._background_tasks.add(task)
    task.add_done_callback(handlers_admin._background_tasks.discard)
    await handlers_admin.cancel_admin_background_tasks()

    assert cancelled is True
    assert task.cancelled()
    assert len(handlers_admin._background_tasks) == 0


def test_b2_verify_admin_session_token_requires_four_parts():
    import hashlib
    import hmac
    import secrets
    import time
    from services import miniapp_auth_service

    with (
        patch.object(miniapp_auth_service, "is_admin", return_value=True),
        patch.object(
            miniapp_auth_service,
            "_derive_signing_key",
            return_value=b"test-admin-session-signing-key-32b",
        ),
    ):
        tok_4, _ = miniapp_auth_service.issue_admin_session_token(1001, epoch="7")
        assert len(tok_4.split(".")) == 4
        assert miniapp_auth_service.verify_admin_session_token(tok_4, 1001, expected_epoch="7") is True
        assert miniapp_auth_service.verify_admin_session_token(tok_4, 1001, expected_epoch="8") is False

        # Legacy 3-part token signed with the same key must be rejected.
        exp_ts = str(int(time.time()) + 300)
        nonce = secrets.token_hex(8)
        payload = f"1001:{exp_ts}:{nonce}"
        sig = hmac.new(
            miniapp_auth_service._derive_signing_key(b"MiniAppAdminSession"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        legacy_tok = f"{exp_ts}.{nonce}.{sig}"
        assert miniapp_auth_service.verify_admin_session_token(legacy_tok, 1001, expected_epoch="7") is False


@pytest.mark.asyncio
async def test_b4_delete_node_cleans_jsonb_subscription_snapshots():
    hub = SimpleNamespace(
        id=13,
        name="Hub 13",
        role="ru_hub",
        capabilities=["ru_hub"],
        is_active=True,
        health_state=ServerHealthState.ONLINE,
        lifecycle_status=ServerLifecycleStatus.ACTIVE,
        extra_data={},
    )
    session = AsyncMock()
    session.execute = AsyncMock()

    with (
        patch.object(admin_nodes_service, "get_server_by_id", AsyncMock(return_value=hub)),
        patch.object(admin_nodes_service, "get_all_servers", AsyncMock(return_value=[hub])),
        patch.object(admin_nodes_service, "delete_server", AsyncMock()),
        patch.object(admin_nodes_service, "_queue_topology_sync", MagicMock()),
        patch.object(admin_nodes_service.AuditService, "log_action", AsyncMock()),
    ):
        res = await admin_nodes_service.delete_node(session, admin_id=1001, server_id=13, force=True)

    assert res == {"deleted_id": 13, "name": "Hub 13"}
    assert session.execute.await_count == 2
    executed_tables = [call.args[0].table.name for call in session.execute.call_args_list]
    assert executed_tables == ["white_internet_subscriptions", "vless_subscriptions"]


@pytest.mark.asyncio
async def test_b5_admin_webhook_retry_and_dismiss_endpoints():
    from services.order_service import OrderService

    admin_ctx = SimpleNamespace(telegram_id=1001)
    valid_payload = {
        "type": "notification",
        "event": "payment.succeeded",
        "object": {
            "id": "pay_5",
            "status": "succeeded",
            "metadata": {"order_id": "ORD-5"},
        },
    }
    missing_payload_row = SimpleNamespace(
        id=4,
        event_key="yk:evt:4",
        provider="yookassa",
        status=WebhookInboxStatus.DEAD.value,
        payload=None,
        public_order_id=None,
    )
    dead_ok_row = SimpleNamespace(
        id=5,
        event_key="yk:evt:5",
        provider="yookassa",
        status=WebhookInboxStatus.DEAD.value,
        payload=valid_payload,
        public_order_id=None,
    )
    fail_not_found_row = SimpleNamespace(
        id=8,
        event_key="yk:evt:8",
        provider="yookassa",
        status=WebhookInboxStatus.RETRY.value,
        payload={
            "type": "notification",
            "event": "payment.succeeded",
            "object": {"id": "pay_8", "status": "succeeded"},
        },
        public_order_id=None,
    )
    fail_exc_row = SimpleNamespace(
        id=9,
        event_key="yk:evt:9",
        provider="yookassa",
        status=WebhookInboxStatus.RETRY.value,
        payload={
            "type": "notification",
            "event": "payment.succeeded",
            "object": {"id": "pay_9", "status": "succeeded"},
        },
        public_order_id=None,
    )
    pending_row = SimpleNamespace(
        id=6,
        event_key="yk:evt:6",
        provider="yookassa",
        status=WebhookInboxStatus.PENDING.value,
        payload=valid_payload,
    )
    retry_dismiss_row = SimpleNamespace(
        id=7,
        event_key="yk:evt:7",
        provider="yookassa",
        status=WebhookInboxStatus.RETRY.value,
    )

    paid_order = SimpleNamespace(
        id=55,
        user_id=1,
        service_type="standard",
        status="paid",
        amount_rub=Decimal("99"),
        metadata_={},
        _newly_paid=False,
    )

    session = AsyncMock()
    session.scalar = AsyncMock(
        side_effect=[
            missing_payload_row,
            dead_ok_row,
            fail_not_found_row,
            fail_exc_row,
            retry_dismiss_row,
            pending_row,
            None,
        ]
    )
    audit_mock = AsyncMock()
    process_mock = AsyncMock(
        side_effect=[paid_order, None, RuntimeError("db error")]
    )

    with (
        patch.object(handlers_admin, "require_admin_user", return_value=(admin_ctx, None)),
        patch.object(handlers_admin, "session_scope", _make_session_scope(session)),
        patch.object(handlers_admin.AuditService, "log_action", audit_mock),
        patch.object(OrderService, "process_webhook_event", process_mock),
    ):
        resp_missing_payload = await handlers_admin.api_admin_webhook_retry_handler(
            SimpleNamespace(app={}, match_info={"webhook_id": "4"})
        )
        resp_retry_ok = await handlers_admin.api_admin_webhook_retry_handler(
            SimpleNamespace(app={}, match_info={"webhook_id": "5"})
        )
        resp_retry_not_found = await handlers_admin.api_admin_webhook_retry_handler(
            SimpleNamespace(app={}, match_info={"webhook_id": "8"})
        )
        resp_retry_exc = await handlers_admin.api_admin_webhook_retry_handler(
            SimpleNamespace(app={}, match_info={"webhook_id": "9"})
        )
        resp_dismiss = await handlers_admin.api_admin_webhook_dismiss_handler(
            SimpleNamespace(match_info={"webhook_id": "7"})
        )
        resp_bad = await handlers_admin.api_admin_webhook_retry_handler(
            SimpleNamespace(app={}, match_info={"webhook_id": "6"})
        )
        resp_missing = await handlers_admin.api_admin_webhook_dismiss_handler(
            SimpleNamespace(match_info={"webhook_id": "999"})
        )

    assert resp_missing_payload.status == 400
    assert missing_payload_row.status == WebhookInboxStatus.DEAD.value

    assert resp_retry_ok.status == 200
    assert json.loads(resp_retry_ok.text)["status"] == WebhookInboxStatus.SUCCEEDED.value
    assert dead_ok_row.status == WebhookInboxStatus.SUCCEEDED.value
    assert dead_ok_row.public_order_id == "ORD-5"

    # Order missing or exception during reprocessing must finalize as DEAD, never stranded in PENDING
    assert resp_retry_not_found.status == 400
    assert json.loads(resp_retry_not_found.text)["status"] == WebhookInboxStatus.DEAD.value
    assert fail_not_found_row.status == WebhookInboxStatus.DEAD.value

    assert resp_retry_exc.status == 502
    assert json.loads(resp_retry_exc.text)["status"] == WebhookInboxStatus.DEAD.value
    assert fail_exc_row.status == WebhookInboxStatus.DEAD.value

    assert resp_dismiss.status == 200
    assert json.loads(resp_dismiss.text)["status"] == WebhookInboxStatus.SUCCEEDED.value
    assert retry_dismiss_row.status == WebhookInboxStatus.SUCCEEDED.value

    assert resp_bad.status == 400
    assert resp_missing.status == 404
    assert audit_mock.await_count == 4


@pytest.mark.asyncio
async def test_b6_worker_age_fields_and_server_now_payloads():
    from datetime import datetime, timezone
    import time
    from bot.miniapp import admin_users_handlers, handlers_user
    import services.workers as workers_mod

    now_m = time.monotonic()
    workers_mod._worker_health["test_b6_worker"] = workers_mod.WorkerHealth(
        state="degraded",
        last_started_at=now_m - 12.5,
        last_finished_at=now_m - 4.0,
        consecutive_failures=1,
        last_error_type="RuntimeError",
        critical=False,
    )
    snap = workers_mod.get_worker_health_snapshot()
    w_info = snap["workers"].pop("test_b6_worker")
    workers_mod._worker_health.pop("test_b6_worker", None)
    assert isinstance(w_info["last_started_age_sec"], float)
    assert w_info["last_started_age_sec"] >= 12.0
    assert isinstance(w_info["last_error_age_sec"], float)
    assert w_info["last_error_age_sec"] >= 3.5

    # User dashboard payload includes server_now, grace_hours, standard_tariff_prices
    now = datetime(2026, 11, 25, 12, 0, 0, tzinfo=timezone.utc)
    fake_user = SimpleNamespace(
        id=42,
        telegram_id=420001,
        username="u42",
        first_name="User42",
        financial_hold=False,
        financial_block_reason=None,
        is_deleted=False,
        created_at=now,
        last_payment_at=None,
        referred_by=None,
        is_bot_blocked=False,
    )
    fake_bal = SimpleNamespace(
        real_available=Decimal("100"),
        bonus_available=Decimal("50"),
        available=Decimal("150"),
        debt=Decimal("0"),
        real_position=Decimal("100"),
    )
    fake_vless = SimpleNamespace(
        token="std-tok-1",
        is_active=True,
        expires_at=now + timedelta(days=5),
        device_limit=3,
        auto_renew_enabled=False,
        downgrade_cooldown_until=None,
        last_synced_at=None,
    )
    user_session = AsyncMock()
    user_req = SimpleNamespace(app={"bot_username": "just1k_test_bot"})

    with (
        patch.object(handlers_user, "require_authenticated_user", return_value=(SimpleNamespace(telegram_id=420001), None)),
        patch.object(handlers_user, "now_utc", return_value=now),
        patch.object(handlers_user, "session_scope", _make_session_scope(user_session)),
        patch.object(handlers_user, "_ensure_user", AsyncMock(return_value=fake_user)),
        patch.object(handlers_user, "get_account_balance", AsyncMock(return_value=fake_bal)),
        patch.object(handlers_user, "get_or_create_subscription", AsyncMock(return_value=fake_vless)),
        patch.object(handlers_user, "get_subscription_by_user_id", AsyncMock(return_value=None)),
        patch.object(handlers_user.SubscriptionService, "check_vpn_access", return_value=True),
        patch.object(handlers_user, "is_eligible_for_referral_first_discount", AsyncMock(return_value=False)),
        patch.object(handlers_user.user_devices_repo, "list_user_devices", AsyncMock(return_value=[])),
        patch.object(handlers_user, "_get_referral_summary", AsyncMock(return_value={"invited": 0})),
        patch.object(handlers_user, "get_account_history", AsyncMock(return_value=[])),
        patch.object(handlers_user, "get_settings", return_value=SimpleNamespace(SUPPORT_USERNAME="@just1k_help", MINIAPP_URL="https://app.example.com", SUBSCRIPTION_DOMAIN="sub.example.com", SUBSCRIPTION_BACKUP_DOMAIN="backup.example.com", DOMAIN="example.com")),
        patch.object(handlers_user, "get_system_setting", AsyncMock(side_effect=["false", ""])),
    ):
        dash_resp = await handlers_user.api_user_dashboard_handler(user_req)

    assert dash_resp.status == 200
    dash_data = json.loads(dash_resp.text)
    assert dash_data["server_now"] == now.isoformat()
    assert dash_data["grace_hours"] == 4
    assert "30" in dash_data["standard_tariff_prices"]
    assert "base_rub" in dash_data["standard_tariff_prices"]["30"]
    assert "step_rub" in dash_data["standard_tariff_prices"]["30"]

    # Admin user details payload includes server_now
    admin_session = AsyncMock()
    admin_session.get = AsyncMock(return_value=fake_user)
    admin_session.scalar = AsyncMock(return_value=0)
    empty_scalars = MagicMock()
    empty_scalars.all.return_value = []
    admin_session.scalars = AsyncMock(return_value=empty_scalars)

    with (
        patch.object(handlers_admin, "require_admin_user", return_value=(SimpleNamespace(telegram_id=1001), None)),
        patch.object(handlers_admin, "session_scope", _make_session_scope(admin_session)),
        patch.object(admin_users_handlers, "now_utc", return_value=now),
        patch.object(admin_users_handlers, "get_account_balance", AsyncMock(return_value=fake_bal)),
        patch.object(handlers_admin.vless_subscription_repo, "get_subscription_by_user_id", AsyncMock(return_value=fake_vless)),
        patch.object(handlers_admin, "get_subscription_by_user_id", AsyncMock(return_value=None)),
        patch.object(handlers_admin.SubscriptionService, "get_device_slot_usage", AsyncMock(return_value={"devices": []})),
        patch.object(admin_users_handlers, "get_account_history", AsyncMock(return_value=[])),
        patch.object(handlers_user, "get_settings", return_value=SimpleNamespace(SUBSCRIPTION_DOMAIN="sub.example.com", SUBSCRIPTION_BACKUP_DOMAIN="backup.example.com", DOMAIN="example.com")),
    ):
        details_resp = await handlers_admin.api_admin_user_details_handler(
            SimpleNamespace(match_info={"user_id": "42"})
        )

    assert details_resp.status == 200
    details_data = json.loads(details_resp.text)
    assert details_data["server_now"] == now.isoformat()


def test_b7_last_apply_and_ssl_cert_persistence_and_serialization():
    from database.repositories import servers_repo

    merged = servers_repo.merge_v2_extra_data(
        {"existing_key": 1},
        status_data={
            "last_apply": {"ok": True, "rc": 0, "role": "exit", "ts": 1700000000},
            "ssl_cert": {"expires_at": "2027-01-01T00:00:00Z", "days_left": 120},
        },
        health_data={},
    )
    assert merged["last_apply"]["ok"] is True
    assert merged["ssl_cert"]["days_left"] == 120

    node = SimpleNamespace(
        id=9,
        name="Node 9",
        role="exit_node",
        capabilities=["exit_node"],
        country_flag="🇪🇪",
        protocol="xray",
        api_url="https://node9.example",
        api_key="k",
        is_active=True,
        disabled_reason=None,
        disabled_at=None,
        health_state=ServerHealthState.ONLINE,
        lifecycle_status=ServerLifecycleStatus.ACTIVE,
        consecutive_fails=0,
        problem_started_at=None,
        xray_instance_epoch="ep9",
        last_successful_check=None,
        created_at=None,
        extra_data=merged,
    )
    serialized = admin_nodes_service.serialize_server_node(node)
    assert serialized["last_apply"] == {"ok": True, "rc": 0, "role": "exit", "ts": 1700000000}
    assert serialized["ssl_cert"] == {"expires_at": "2027-01-01T00:00:00Z", "days_left": 120}


@pytest.mark.asyncio
async def test_b8_wi_traffic_alerts_attach_tariffs_keyboard():
    from services.workers import traffic as traffic_worker

    fake_user = SimpleNamespace(id=77, telegram_id=77001, is_bot_blocked=False)
    session = AsyncMock()
    session.scalar = AsyncMock(return_value=fake_user)
    session.execute = AsyncMock()
    send_mock = AsyncMock()
    bot = MagicMock()

    with (
        patch.object(traffic_worker, "session_scope", _make_session_scope(session)),
        patch.object(traffic_worker.global_send_limiter, "acquire", AsyncMock()),
        patch.object(traffic_worker, "safe_send_message", send_mock),
        patch.object(traffic_worker, "get_action_notification_keyboard", return_value="KB_TARIFFS") as kb_mock,
    ):
        await traffic_worker._send_wi_exhausted_alerts(bot, [77])
        await traffic_worker._send_wi_90p_alerts(bot, [(10, 77, 3)])

    assert kb_mock.call_count == 2
    assert all(c.args == ("Открыть тарифы",) and c.kwargs == {"tab": "tariffs"} for c in kb_mock.call_args_list)
    assert send_mock.await_count == 2
    assert all(c.kwargs.get("reply_markup") == "KB_TARIFFS" for c in send_mock.await_args_list)


@pytest.mark.asyncio
async def test_b10_miniapp_asset_version_rewrite_and_webhook_routes():
    from aiohttp import web
    from bot.miniapp import routes

    assert isinstance(routes._MINIAPP_ASSET_VERSION, str)
    assert len(routes._MINIAPP_ASSET_VERSION) == 10
    assert routes._MINIAPP_ASSET_VERSION != "2.8.0"

    req = SimpleNamespace(remote="127.0.0.1", headers={}, match_info={"asset_path": "js/app.js"})
    with (
        patch.object(routes, "check_miniapp_rate_limit", return_value=None),
        patch.object(routes, "check_miniapp_asset_rate_limit", return_value=None),
    ):
        idx_resp = await routes.miniapp_index_handler(req)
        js_resp = await routes.miniapp_asset_handler(req)

    assert idx_resp.status == 200
    assert f"?v={routes._MINIAPP_ASSET_VERSION}" in idx_resp.text
    assert "?v=2.8.0" not in idx_resp.text

    assert js_resp.status == 200
    assert f"?v={routes._MINIAPP_ASSET_VERSION}" in js_resp.text
    assert "?v=2.8.0" not in js_resp.text

    app = web.Application()
    routes.setup_miniapp_routes(app)
    registered_paths = {r.resource.canonical for r in app.router.routes()}
    assert "/api/app/admin/webhooks/{webhook_id}/retry" in registered_paths
    assert "/api/app/admin/webhooks/{webhook_id}/dismiss" in registered_paths


def _make_v2_topology_node(
    node_id: int,
    role: str,
    ip: str,
    domain: str,
    bridge_uuid: str,
    *,
    lifecycle_status: ServerLifecycleStatus = ServerLifecycleStatus.ACTIVE,
    extra_overrides: dict | None = None,
):
    extra = {
        "role": role,
        "ip": ip,
        "domain": domain,
        "bridge_uuid": bridge_uuid,
    }
    if extra_overrides:
        extra.update(extra_overrides)
    return SimpleNamespace(
        id=node_id,
        name=f"{role}-{node_id}",
        role=role,
        capabilities=[role],
        protocol="xray",
        api_url=f"https://{domain}",
        api_key="k",
        is_active=True,
        health_state=ServerHealthState.ONLINE,
        lifecycle_status=lifecycle_status,
        extra_data=extra,
    )


def test_p0_1_build_v2_topology_links_decommissioning_ru_vs_eu():
    import uuid
    from services.topology_sync_service import build_v2_topology_links

    ru_active = _make_v2_topology_node(
        1, "ru_hub", "10.0.0.1", "ru1.example", str(uuid.UUID(int=1)),
        lifecycle_status=ServerLifecycleStatus.ACTIVE,
    )
    ru_decomm = _make_v2_topology_node(
        2, "ru_hub", "10.0.0.2", "ru2.example", str(uuid.UUID(int=2)),
        lifecycle_status=ServerLifecycleStatus.DECOMMISSIONING,
    )
    eu_active = _make_v2_topology_node(
        10, "eu_hub", "20.0.0.10", "eu10.example", str(uuid.UUID(int=10)),
        lifecycle_status=ServerLifecycleStatus.ACTIVE,
    )
    eu_decomm = _make_v2_topology_node(
        11, "eu_hub", "20.0.0.11", "eu11.example", str(uuid.UUID(int=11)),
        lifecycle_status=ServerLifecycleStatus.DECOMMISSIONING,
    )
    exit_1 = _make_v2_topology_node(
        20, "exit_node", "30.0.0.20", "ex20.example", str(uuid.UUID(int=20)),
        lifecycle_status=ServerLifecycleStatus.ACTIVE,
        extra_overrides={"location_code": "nl", "in_standard": True, "in_white_internet": True},
    )

    payloads = build_v2_topology_links([ru_active, ru_decomm, eu_active, eu_decomm, exit_1])

    # DECOMMISSIONING ru_hub stays eligible for its own links.json and eu_hub allowlist
    assert ru_active.id in payloads
    assert ru_decomm.id in payloads
    assert eu_active.id in payloads
    assert eu_decomm.id not in payloads

    eu_payload = payloads[eu_active.id]
    assert set(eu_payload["allowed_ru_ips"]) == {"10.0.0.1", "10.0.0.2"}
    assert {h["code"] for h in eu_payload["incoming_ru_hubs"]} == {"ru1", "ru2"}

    # DECOMMISSIONING eu_hub is excluded from exit_node and ru_hub bridges
    exit_payload = payloads[exit_1.id]
    assert exit_payload["allowed_hub_ips"] == ["20.0.0.10"]
    assert [h["code"] for h in exit_payload["incoming_hubs"]] == ["eu10"]
    for rh_id in (ru_active.id, ru_decomm.id):
        assert [b["ip"] for b in payloads[rh_id]["standard"]["bridges"]] == ["20.0.0.10"]
        assert [b["ip"] for b in payloads[rh_id]["bi"]["bridges"]] == ["20.0.0.10"]


def test_p1_3_miniapp_auth_non_ascii_hash_and_sig_return_false_none():
    import time
    from urllib.parse import urlencode
    from services import miniapp_auth_service

    now_ts = int(time.time())
    init_data = urlencode({
        "auth_date": str(now_ts),
        "user": json.dumps({"id": 123456, "first_name": "Test"}),
        "hash": "неаски_хэш_🔥",
    })
    assert miniapp_auth_service.validate_telegram_init_data(init_data, "123456:BOT_TOKEN") is None

    future_exp = now_ts + 600
    bad_tok = f"{future_exp}.deadbeef.1.подпись🔥"
    with (
        patch.object(miniapp_auth_service, "is_admin", return_value=True),
        patch.object(
            miniapp_auth_service,
            "_derive_signing_key",
            return_value=b"test-admin-session-signing-key-32b",
        ),
    ):
        assert miniapp_auth_service.verify_admin_session_token(bad_tok, 1001, expected_epoch="1") is False


def test_p1_5_p1_6_build_v2_topology_links_bridge_code_and_blocked_eu_failsafe():
    import uuid
    from services.topology_sync_service import build_v2_topology_links

    long_loc = "a" * 32
    eu_1 = _make_v2_topology_node(12, "eu_hub", "20.0.0.12", "eu12.example", str(uuid.UUID(int=12)))
    eu_2 = _make_v2_topology_node(13, "eu_hub", "20.0.0.13", "eu13.example", str(uuid.UUID(int=13)))

    ex_1 = _make_v2_topology_node(
        21, "exit_node", "30.0.0.21", "ex21.example", str(uuid.UUID(int=21)),
        extra_overrides={"location_code": long_loc, "in_standard": True, "in_white_internet": True},
    )
    ex_2 = _make_v2_topology_node(
        22, "exit_node", "30.0.0.22", "ex22.example", str(uuid.UUID(int=22)),
        extra_overrides={"location_code": "de", "in_standard": True, "in_white_internet": True},
    )
    ex_3 = _make_v2_topology_node(
        23, "exit_node", "30.0.0.23", "ex23.example", str(uuid.UUID(int=23)),
        extra_overrides={"location_code": "fi", "in_standard": True, "in_white_internet": True},
    )

    ru_normal = _make_v2_topology_node(1, "ru_hub", "10.0.0.1", "ru1.example", str(uuid.UUID(int=1)))
    ru_blocked_one = _make_v2_topology_node(
        2, "ru_hub", "10.0.0.2", "ru2.example", str(uuid.UUID(int=2)),
        extra_overrides={"blocked_eu_ids": [eu_1.id]},
    )
    ru_blocked_all = _make_v2_topology_node(
        3, "ru_hub", "10.0.0.3", "ru3.example", str(uuid.UUID(int=3)),
        extra_overrides={"blocked_eu_ids": [eu_1.id, eu_2.id]},
    )

    payloads = build_v2_topology_links([ru_normal, ru_blocked_one, ru_blocked_all, eu_1, eu_2, ex_1, ex_2, ex_3])

    # P1-5: 32-char location_code produces code <= 32 chars ending with -eu{id}
    long_loc_bridges = [
        b for b in payloads[ru_normal.id]["standard"]["bridges"] if b["location_code"] == long_loc
    ]
    assert len(long_loc_bridges) == 2
    for b in long_loc_bridges:
        assert len(b["code"]) <= 32
        assert b["code"].endswith(("-eu12", "-eu13"))

    # P1-6: blocked_eu_ids excludes blocked eu_hub from standard/bi into probe_bridges (up to 2 exits)
    one_payload = payloads[ru_blocked_one.id]
    assert {b["ip"] for b in one_payload["standard"]["bridges"]} == {"20.0.0.13"}
    assert {b["ip"] for b in one_payload["bi"]["bridges"]} == {"20.0.0.13"}
    assert len(one_payload["probe_bridges"]) == 2
    assert all(pb["ip"] == "20.0.0.12" and pb["is_default"] is False for pb in one_payload["probe_bridges"])
    assert one_payload["probe_bridges"][0]["code"].endswith("-eu12")
    assert len(one_payload["probe_bridges"][0]["code"]) <= 32

    # P1-6 fail-safe: when all eu_hubs are blocked, keep routed_eu = valid_eu and omit probe_bridges
    all_payload = payloads[ru_blocked_all.id]
    assert {b["ip"] for b in all_payload["standard"]["bridges"]} == {"20.0.0.12", "20.0.0.13"}
    assert {b["ip"] for b in all_payload["bi"]["bridges"]} == {"20.0.0.12", "20.0.0.13"}
    assert len(all_payload["standard"]["bridges"]) == 6
    assert "probe_bridges" not in all_payload


@pytest.mark.asyncio
async def test_p1_10_quote_standard_change_four_hour_grace_period():
    from services.order_service import OrderService

    now = now_utc()
    session = AsyncMock()
    session.get = AsyncMock(return_value=None)

    # Within 4h grace period (expires_at = now - 2h): renew and downgrade both report in_grace=True
    grace_sub = SimpleNamespace(
        expires_at=now - timedelta(hours=2),
        device_limit=3,
        downgrade_cooldown_until=None,
    )
    with (
        patch("services.order_service.now_utc", return_value=now),
        patch(
            "database.repositories.vless_subscription_repo.get_subscription_by_user_id",
            AsyncMock(return_value=grace_sub),
        ),
        patch("services.order_service.count_active_user_devices", AsyncMock(return_value=1)),
        patch.object(
            OrderService,
            "_get_standard_downgrade_cap",
            AsyncMock(return_value=(100, Decimal("1"))),
        ),
    ):
        renew_quote = await OrderService.quote_standard_change(
            session, user_id=1, target_device_limit=3, target_duration_days=30, operation="renew"
        )
        downgrade_quote = await OrderService.quote_standard_change(
            session, user_id=1, target_device_limit=2, target_duration_days=30
        )

    assert renew_quote["operation"] == "renew"
    assert renew_quote["in_grace"] is True
    assert renew_quote["days_left"] == 0

    assert downgrade_quote["operation"] == "downgrade"
    assert downgrade_quote["in_grace"] is True
    assert downgrade_quote["can_downgrade"] is True
    assert downgrade_quote["days_left"] == 0
    assert downgrade_quote["bonus_credit_rub"] == 0

    # Past 4h grace period (expires_at = now - 5h): downgrade falls back to purchase with in_grace=False
    expired_sub = SimpleNamespace(
        expires_at=now - timedelta(hours=5),
        device_limit=3,
        downgrade_cooldown_until=None,
    )
    with (
        patch("services.order_service.now_utc", return_value=now),
        patch(
            "database.repositories.vless_subscription_repo.get_subscription_by_user_id",
            AsyncMock(return_value=expired_sub),
        ),
        patch("services.order_service.count_active_user_devices", AsyncMock(return_value=1)),
        patch("services.order_service.is_eligible_for_referral_first_discount", AsyncMock(return_value=False)),
    ):
        past_grace_downgrade = await OrderService.quote_standard_change(
            session, user_id=1, target_device_limit=2, target_duration_days=30
        )

    assert past_grace_downgrade["operation"] == "purchase"
    assert past_grace_downgrade["in_grace"] is False


@pytest.mark.asyncio
async def test_p2_2_node_monitor_bridge_health_blocking_dedup_and_recovery():
    from services.workers import node_monitor

    ru_hub = SimpleNamespace(
        id=1,
        name="RU Hub 1",
        role="ru_hub",
        protocol="xray",
        api_url="https://ru1.example",
        api_key="k1",
        is_active=True,
        health_state=ServerHealthState.ONLINE,
        lifecycle_status=ServerLifecycleStatus.ACTIVE,
        consecutive_fails=0,
        consecutive_successes=0,
        last_alert_sent_state=None,
        problem_started_at=None,
        next_check_at=None,
        disabled_reason=None,
        last_successful_check=None,
        capabilities=["ru_hub"],
        extra_data={"role": "ru_hub", "ip": "1.1.1.1"},
        xray_instance_epoch=None,
        xray_instance_boot_id=None,
        xray_instance_starttime=None,
    )
    eu_hub = SimpleNamespace(
        id=2,
        name="EU Hub 2",
        role="eu_hub",
        protocol="xray",
        api_url="https://eu2.example",
        api_key="k2",
        is_active=True,
        health_state=ServerHealthState.ONLINE,
        lifecycle_status=ServerLifecycleStatus.ACTIVE,
        consecutive_fails=0,
        consecutive_successes=0,
        last_alert_sent_state=None,
        problem_started_at=None,
        next_check_at=None,
        disabled_reason=None,
        last_successful_check=None,
        capabilities=["eu_hub"],
        extra_data={"role": "eu_hub", "ip": "2.2.2.2", "domain": "eu2.example"},
        xray_instance_epoch=None,
        xray_instance_boot_id=None,
        xray_instance_starttime=None,
    )

    async def _run_tick(bridge_probe: dict):
        sess = AsyncMock()
        nonblocking_mock = AsyncMock(return_value={1: True, 2: True})

        async def _fake_check_health(url, _key):
            if "ru1.example" in url:
                return True, None, {"bridge_health": {"2.2.2.2": bridge_probe}}
            return True, None, {}

        fake_xray = SimpleNamespace(check_health=AsyncMock(side_effect=_fake_check_health))

        @asynccontextmanager
        async def _xray_ctx(*_a, **_kw):
            yield fake_xray

        async def _fake_get_by_id(_sess, sid, **_kw):
            return ru_hub if sid == 1 else eu_hub

        async def _fake_update_server(_sess, srv, **kwargs):
            for k, v in kwargs.items():
                setattr(srv, k, v)
            return srv

        async def _fake_health_snap(_sess, sid, **_kw):
            return (ru_hub if sid == 1 else eu_hub), True

        with (
            patch.object(node_monitor, "session_scope", _make_session_scope(sess)),
            patch.object(node_monitor, "get_all_servers", AsyncMock(return_value=[ru_hub, eu_hub])),
            patch.object(node_monitor, "get_server_by_id", AsyncMock(side_effect=_fake_get_by_id)),
            patch.object(node_monitor, "update_server", AsyncMock(side_effect=_fake_update_server)),
            patch.object(node_monitor, "update_server_health_snapshot", AsyncMock(side_effect=_fake_health_snap)),
            patch.object(node_monitor, "_send_admin_alert_msg", AsyncMock(return_value=True)),
            patch("services.xray_node_client.XrayNodeClient", _xray_ctx),
            patch("services.topology_sync_service.sync_v2_topology_to_nodes_nonblocking", nonblocking_mock),
        ):
            await node_monitor.check_node_resources_and_alerts(bot=None)
        return nonblocking_mock

    node_monitor._server_states.clear()
    try:
        # Tick 1: 1st failure -> not blocked yet, no topology sync
        nb1 = await _run_tick({"ok": False, "error": "timeout", "checked_at": 1000.0})
        assert ru_hub.extra_data["bridge_health_state"]["2"]["fails"] == 1
        assert "blocked_eu_ids" not in ru_hub.extra_data
        nb1.assert_not_awaited()

        # Tick 2: duplicate checked_at sample must not be double-counted
        nb2 = await _run_tick({"ok": False, "error": "timeout", "checked_at": 1000.0})
        assert ru_hub.extra_data["bridge_health_state"]["2"]["fails"] == 1
        assert "blocked_eu_ids" not in ru_hub.extra_data
        nb2.assert_not_awaited()

        # Tick 3: 2nd distinct failure -> blocks eu_hub=2 and triggers topology sync
        nb3 = await _run_tick({"ok": False, "error": "timeout", "checked_at": 1060.0})
        assert ru_hub.extra_data["blocked_eu_ids"] == [2]
        assert ru_hub.extra_data["bridge_health_state"]["2"]["blocked"] is True
        nb3.assert_awaited_once()

        # Tick 4: 1st success -> remains blocked until 2 consecutive successes
        nb4 = await _run_tick({"ok": True, "checked_at": 1120.0})
        assert ru_hub.extra_data["blocked_eu_ids"] == [2]
        assert ru_hub.extra_data["bridge_health_state"]["2"]["successes"] == 1
        nb4.assert_not_awaited()

        # Tick 5: 2nd distinct success -> unblocks eu_hub=2 and triggers topology sync
        nb5 = await _run_tick({"ok": True, "checked_at": 1180.0})
        assert "blocked_eu_ids" not in ru_hub.extra_data
        assert ru_hub.extra_data["bridge_health_state"]["2"]["blocked"] is False
        nb5.assert_awaited_once()
    finally:
        node_monitor._server_states.clear()
