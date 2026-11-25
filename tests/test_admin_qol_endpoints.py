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

    # Case 1: EXPIRED WI subscription must stay SYNCED_INACTIVE and not queue inline sync
    resp_exp, wi_exp, queue_exp, deprov_exp, wi_disp_exp = await _run_case(
        WhiteInternetStatus.EXPIRED,
        WhiteInternetProvisioningStatus.SYNCED_INACTIVE,
    )
    assert resp_exp.status == 200
    assert wi_exp.provisioning_status == WhiteInternetProvisioningStatus.SYNCED_INACTIVE
    assert wi_exp.desired_version == 5
    queue_exp.assert_not_awaited()
    assert deprov_exp.call_count == 1
    assert wi_disp_exp.call_count == 1

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

    # Case 1: WI subscription is reactivated to ACTIVE BEFORE held orders are released
    user_1 = SimpleNamespace(
        id=51,
        telegram_id=5100,
        is_deleted=False,
        financial_hold=True,
        topup_blocked=False,
        financial_block_reason="dispute",
    )
    wi_1 = SimpleNamespace(
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
    held_order = SimpleNamespace(id="ord-held-1", service_type="white_internet")

    session_1 = AsyncMock()
    session_1.get = AsyncMock(return_value=user_1)
    session_1.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: user_1))
    session_1.scalar = AsyncMock(return_value=None)
    session_1.scalars = AsyncMock(return_value=SimpleNamespace(all=lambda: [held_order]))

    status_at_order_release: list[str] = []

    async def _fake_mark_paid(_sess, _order_id):
        status_at_order_release.append(str(wi_1.status))
        return held_order

    queue_wi_1 = AsyncMock()
    req_1 = SimpleNamespace(
        match_info={"user_id": "51"},
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
    assert status_at_order_release == [WhiteInternetStatus.ACTIVE.value]
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
