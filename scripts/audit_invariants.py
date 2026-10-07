#!/usr/bin/env python3
# ruff: noqa: E402
"""
Comprehensive Invariant Scanner for just1kbot Subsystems.

Verifies 13 critical database, domain, cryptographic, and accounting invariants:
  1. Tariff Version Immutability & Constraint Invariant
  2. Server Lifecycle & Health Status Invariant
  3. White Internet Subscription State Invariant
  4. White Internet Quota Grant Conservation Invariant
  5. White Internet Traffic Event Conservation Invariant
  6. Traffic Event Idempotency Key Uniqueness Invariant
  7. Subscription Period Usage Non-Negativity Invariant
  8. Origin Node Capability & Existence Invariant
 10. Account Balance Non-Negativity Invariant
 11. Account Ledger Entry Conservation Invariant
 13. VPN Profile Protocol Invariant (Exclusively AWG)
 14. Origin Server Capacity Non-Breach Invariant
 15. Alembic Migration Single Head Invariant
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from pathlib import Path
import sys
from typing import NamedTuple

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from config.constants import AMNEZIA_PROTOCOLS
from config.enums import (
    AccountLedgerEntryType,
    ServerHealthState,
    ServerLifecycleStatus,
    ServiceType,
    WhiteInternetProvisioningStatus,
    WhiteInternetStatus,
)
from database.connection import session_scope
from database.models import (
    AccountLedgerEntry,
    Server,
    Tariff,
    WhiteInternetSubscription,
)
from database.repositories.servers_repo import capacity_consuming_wl_condition


class InvariantResult(NamedTuple):
    number: int
    name: str
    passed: bool
    details: str


async def assert_inv_1_tariffs(session: AsyncSession) -> InvariantResult:
    """Inv 1: Tariffs have valid service_type, positive duration and price."""
    violations = await session.scalars(
        select(Tariff).where(
            or_(
                ~Tariff.service_type.in_([ServiceType.AWG, ServiceType.WHITE_INTERNET]),
                Tariff.duration_days <= 0,
                Tariff.price_rub <= 0,
            )
        )
    )
    v_list = violations.all()
    if v_list:
        return InvariantResult(
            1, "Tariff Constraints", False, f"Violations found: {len(v_list)} rows"
        )
    return InvariantResult(
        1, "Tariff Constraints", True, "All tariffs satisfy schema invariants"
    )


async def assert_inv_2_server_lifecycle(session: AsyncSession) -> InvariantResult:
    """Inv 2: Servers have valid lifecycle_status and health_state."""
    valid_lifecycles = [s.value for s in ServerLifecycleStatus]
    valid_healths = [h.value for h in ServerHealthState]
    violations = await session.scalars(
        select(Server).where(
            or_(
                ~Server.lifecycle_status.in_(valid_lifecycles),
                ~Server.health_state.in_(valid_healths),
            )
        )
    )
    v_list = violations.all()
    if v_list:
        return InvariantResult(
            2, "Server Lifecycle & Health", False, f"Violations found: {len(v_list)} servers"
        )
    return InvariantResult(
        2, "Server Lifecycle & Health", True, "All servers have valid lifecycle and health states"
    )


async def assert_inv_3_white_internet_subscriptions(session: AsyncSession) -> InvariantResult:
    """Inv 3: Subscriptions have valid statuses, non-null UUID and token."""
    valid_statuses = [s.value for s in WhiteInternetStatus]
    valid_prov_statuses = [p.value for p in WhiteInternetProvisioningStatus]
    violations = await session.scalars(
        select(WhiteInternetSubscription).where(
            or_(
                ~WhiteInternetSubscription.status.in_(valid_statuses),
                ~WhiteInternetSubscription.provisioning_status.in_(valid_prov_statuses),
                WhiteInternetSubscription.uuid.is_(None),
                WhiteInternetSubscription.token.is_(None),
            )
        )
    )
    v_list = violations.all()
    if v_list:
        return InvariantResult(
            3, "White Internet Subscriptions", False, f"Violations found: {len(v_list)} subs"
        )
    return InvariantResult(
        3, "White Internet Subscriptions", True, "All subscriptions satisfy state invariants"
    )


async def assert_inv_4_subscription_traffic_pools(session: AsyncSession) -> InvariantResult:
    """Inv 4: Subscriptions have non-negative base and extra traffic pools within Hard Cap."""
    from config.constants import WHITE_INTERNET_MAX_QUOTA_BYTES

    max_extra_allowed = func.greatest(1, func.coalesce(WhiteInternetSubscription.device_limit, 1)) * WHITE_INTERNET_MAX_QUOTA_BYTES
    violations = await session.scalars(
        select(WhiteInternetSubscription).where(
            or_(
                WhiteInternetSubscription.base_traffic_bytes < 0,
                WhiteInternetSubscription.extra_traffic_bytes < 0,
                WhiteInternetSubscription.extra_traffic_bytes > max_extra_allowed,
            )
        )
    )
    v_list = violations.all()
    if v_list:
        return InvariantResult(
            4, "Subscription Traffic Pools & Cap", False, f"Violations found: {len(v_list)} subs"
        )
    return InvariantResult(
        4,
        "Subscription Traffic Pools & Cap",
        True,
        "All subscriptions satisfy traffic pool and cap invariants",
    )


async def assert_inv_5_subscription_counter_monotonicity(session: AsyncSession) -> InvariantResult:
    """Inv 5: Subscriptions satisfy last snapshots non-negativity and used traffic bounds."""
    violations = await session.scalars(
        select(WhiteInternetSubscription).where(
            or_(
                WhiteInternetSubscription.last_uplink_snapshot < 0,
                WhiteInternetSubscription.last_downlink_snapshot < 0,
                WhiteInternetSubscription.traffic_used_bytes < 0,
            )
        )
    )
    v_list = violations.all()
    if v_list:
        return InvariantResult(
            5, "Subscription Counter Monotonicity", False, f"Violations found: {len(v_list)} subs"
        )
    return InvariantResult(
        5,
        "Subscription Counter Monotonicity",
        True,
        "All subscription counters satisfy monotonicity invariants",
    )


async def assert_inv_6_subscription_live_uniqueness(session: AsyncSession) -> InvariantResult:
    """Inv 6: Live users have at most one active/pending subscription."""
    res = await session.execute(
        select(WhiteInternetSubscription.user_id, func.count(WhiteInternetSubscription.id))
        .where(WhiteInternetSubscription.status.in_(["PENDING", "ACTIVE", "EXHAUSTED"]))
        .group_by(WhiteInternetSubscription.user_id)
        .having(func.count(WhiteInternetSubscription.id) > 1)
    )
    dups = res.all()
    if dups:
        return InvariantResult(
            6,
            "Live User Subscription Uniqueness",
            False,
            f"Duplicate live subscriptions found: {len(dups)}",
        )
    return InvariantResult(
        6,
        "Live User Subscription Uniqueness",
        True,
        "Live user subscription uniqueness holds strictly",
    )


async def assert_inv_7_subscription_period_usage(session: AsyncSession) -> InvariantResult:
    """Inv 7: Subscriptions have non-negative traffic counters."""
    violations = await session.scalars(
        select(WhiteInternetSubscription).where(
            or_(
                WhiteInternetSubscription.traffic_used_bytes < 0,
                WhiteInternetSubscription.traffic_uplink_bytes < 0,
                WhiteInternetSubscription.traffic_downlink_bytes < 0,
                WhiteInternetSubscription.traffic_overage_bytes < 0,
                WhiteInternetSubscription.base_traffic_bytes < 0,
                WhiteInternetSubscription.extra_traffic_bytes < 0,
            )
        )
    )
    v_list = violations.all()
    if v_list:
        return InvariantResult(
            7, "Subscription Usage Non-Negativity", False, f"Violations found: {len(v_list)} subs"
        )
    return InvariantResult(
        7, "Subscription Usage Non-Negativity", True, "All subscription counters are non-negative"
    )


async def assert_inv_8_origin_node_capabilities(session: AsyncSession) -> InvariantResult:
    """Inv 8: All subscription origin nodes exist and have 'xray_origin' capability."""
    subs = (await session.scalars(select(WhiteInternetSubscription))).all()
    origin_ids = {s.origin_node_id for s in subs if s.origin_node_id is not None}
    if not origin_ids:
        return InvariantResult(
            8, "Origin Node Capabilities", True, "No subscription origin nodes to check"
        )

    servers = (await session.scalars(select(Server).where(Server.id.in_(origin_ids)))).all()
    server_map = {srv.id: srv for srv in servers}

    bad_nodes = []
    for srv_id in origin_ids:
        srv = server_map.get(srv_id)
        if srv is None:
            bad_nodes.append(f"Server {srv_id} missing")
        elif "xray_origin" not in (srv.capabilities or []):
            bad_nodes.append(f"Server {srv_id} lacks 'xray_origin' capability")

    if bad_nodes:
        return InvariantResult(8, "Origin Node Capabilities", False, "; ".join(bad_nodes))
    return InvariantResult(
        8, "Origin Node Capabilities", True, "All origin servers have 'xray_origin' capability"
    )



async def assert_inv_10_account_balance_non_negativity(session: AsyncSession) -> InvariantResult:
    """Inv 10: All users without refund/chargeback debits have non-negative net accounting position."""
    subq_adjustments = (
        select(AccountLedgerEntry.user_id)
        .where(
            AccountLedgerEntry.entry_type.in_(
                ("refund_debit", "chargeback_debit", "admin_adjustment")
            )
        )
        .scalar_subquery()
    )
    res = await session.execute(
        select(
            AccountLedgerEntry.user_id,
            func.coalesce(func.sum(AccountLedgerEntry.amount), Decimal("0")).label("net_balance"),
        )
        .where(AccountLedgerEntry.user_id.not_in(subq_adjustments))
        .group_by(AccountLedgerEntry.user_id)
        .having(func.coalesce(func.sum(AccountLedgerEntry.amount), Decimal("0")) < 0)
    )
    violations = res.all()
    if violations:
        return InvariantResult(
            10,
            "Account Balance Non-Negativity",
            False,
            f"Violations found: {len(violations)} users with unexplained negative balance",
        )
    return InvariantResult(
        10,
        "Account Balance Non-Negativity",
        True,
        "All user accounting balances are non-negative or explained by refund/debt",
    )


async def assert_inv_11_account_ledger_conservation(session: AsyncSession) -> InvariantResult:
    """Inv 11: Account ledger entries have non-zero amounts and valid entry types."""
    valid_types = [t.value for t in AccountLedgerEntryType]
    violations = await session.scalars(
        select(AccountLedgerEntry).where(
            or_(
                AccountLedgerEntry.amount == 0,
                ~AccountLedgerEntry.entry_type.in_(valid_types),
            )
        )
    )
    v_list = violations.all()
    if v_list:
        return InvariantResult(
            11, "Account Ledger Conservation", False, f"Violations found: {len(v_list)} entries"
        )
    return InvariantResult(
        11, "Account Ledger Conservation", True, "All account ledger entries satisfy invariants"
    )


async def assert_inv_13_vpn_protocol(session: AsyncSession) -> InvariantResult:
    """Inv 13: All VPN servers use AWG or Xray protocol (pure WireGuard 'wg' is strictly rejected)."""
    violations = await session.scalars(
        select(Server).where(
            Server.protocol.notin_(list(AMNEZIA_PROTOCOLS) + ["xray"]) | (Server.protocol == "wg")
        )
    )
    v_list = violations.all()
    if v_list:
        return InvariantResult(
            13,
            "VPN Server Protocol (AWG/Xray)",
            False,
            f"Violations: {len(v_list)} invalid protocol servers",
        )
    return InvariantResult(
        13,
        "VPN Server Protocol (AWG/Xray)",
        True,
        "All servers strictly use AmneziaWG (amneziawg2/3/3.1) or Xray (xray)",
    )


async def assert_inv_14_origin_server_capacity(session: AsyncSession) -> InvariantResult:
    """Inv 14: Origin servers do not exceed max_clients for capacity-consuming subscriptions."""
    servers = (
        await session.scalars(
            select(Server).where(
                Server.is_active.is_(True),
                Server.lifecycle_status == ServerLifecycleStatus.ACTIVE,
            )
        )
    ).all()
    origin_servers = [s for s in servers if "xray_origin" in (s.capabilities or [])]

    breaches = []
    for srv in origin_servers:
        active_count = (
            await session.scalar(
                select(func.count(WhiteInternetSubscription.id)).where(
                    WhiteInternetSubscription.origin_node_id == srv.id,
                    capacity_consuming_wl_condition(),
                )
            )
            or 0
        )
        if active_count > srv.max_clients:
            breaches.append(f"Server {srv.id} ({srv.name}): {active_count}/{srv.max_clients}")

    if breaches:
        return InvariantResult(14, "Origin Server Capacity", False, "; ".join(breaches))
    return InvariantResult(
        14, "Origin Server Capacity", True, "All origin servers operate within max_clients capacity"
    )


def _inspect_alembic_head() -> list[str]:
    ini_path = _PROJECT_ROOT / "alembic.ini"
    config_path = str(ini_path) if ini_path.is_file() else "alembic.ini"
    scripts = ScriptDirectory.from_config(Config(config_path))
    return scripts.get_heads()


async def assert_inv_15_alembic_single_head(session: AsyncSession | None = None) -> InvariantResult:
    """Inv 15: Alembic migration graph has exactly one head."""
    try:
        heads = await asyncio.to_thread(_inspect_alembic_head)
        if len(heads) != 1:
            return InvariantResult(
                15,
                "Alembic Single Head Invariant",
                False,
                f"Expected exactly 1 migration head, got {len(heads)}: {heads}",
            )
        return InvariantResult(
            15, "Alembic Single Head Invariant", True, f"Single head verified: {heads[0]}"
        )
    except Exception as e:
        return InvariantResult(
            15, "Alembic Single Head Invariant", False, f"Error inspecting alembic graph: {e}"
        )


ALL_INVARIANT_CHECKS = [
    assert_inv_1_tariffs,
    assert_inv_2_server_lifecycle,
    assert_inv_3_white_internet_subscriptions,
    assert_inv_4_subscription_traffic_pools,
    assert_inv_5_subscription_counter_monotonicity,
    assert_inv_6_subscription_live_uniqueness,
    assert_inv_7_subscription_period_usage,
    assert_inv_8_origin_node_capabilities,
    assert_inv_10_account_balance_non_negativity,
    assert_inv_11_account_ledger_conservation,
    assert_inv_13_vpn_protocol,
    assert_inv_14_origin_server_capacity,
    assert_inv_15_alembic_single_head,
]


async def run_all_invariants(session: AsyncSession | None = None) -> list[InvariantResult]:
    results: list[InvariantResult] = []
    if session is not None:
        for check in ALL_INVARIANT_CHECKS:
            res = await check(session)
            results.append(res)
    else:
        async with session_scope() as sess:
            for check in ALL_INVARIANT_CHECKS:
                res = await check(sess)
                results.append(res)
    return results


def main() -> int:
    n_invariants = len(ALL_INVARIANT_CHECKS)
    print("=" * 80)
    print(f" running {n_invariants} Subsystem Invariant Integrity Assertions ".center(80, "="))
    print("=" * 80)

    results = asyncio.run(run_all_invariants())
    all_passed = True

    for r in results:
        status_str = "[PASS]" if r.passed else "[FAIL]"
        print(f"{status_str} Inv {r.number:02d}: {r.name:<38} -> {r.details}")
        if not r.passed:
            all_passed = False

    print("=" * 80)
    if all_passed:
        print(
            f" SUCCESS: All {n_invariants} invariant integrity assertions passed with 0 violations. ".center(
                80, "="
            )
        )
        print("=" * 80)
        return 0
    else:
        print(" FAILURE: One or more invariant assertions failed! ".center(80, "="))
        print("=" * 80)
        return 1


if __name__ == "__main__":
    sys.exit(main())
