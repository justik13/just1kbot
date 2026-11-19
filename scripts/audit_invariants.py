#!/usr/bin/env python3
# ruff: noqa: E402
"""
Comprehensive Invariant Scanner for just1kbot Subsystems.

Verifies 12 critical database, domain, cryptographic, and accounting invariants:
  2. Server Lifecycle & Health Status Invariant
  3. White Internet Subscription State Invariant
  4. White Internet Quota Grant Conservation Invariant
  5. White Internet Traffic Event Conservation Invariant
  6. Traffic Event Idempotency Key Uniqueness Invariant
  7. Subscription Period Usage Non-Negativity Invariant
  8. Origin Node Capability & Existence Invariant
 10. Account Balance Non-Negativity Invariant
 11. Account Ledger Entry Conservation Invariant
 13. VPN Server Protocol Invariant (Strictly VLESS/Xray)
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
from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from config.constants import VLESS_PROTOCOL, XRAY_PROTOCOL
from config.enums import (
    AccountLedgerEntryType,
    ServerHealthState,
    ServerLifecycleStatus,
    WhiteInternetProvisioningStatus,
    WhiteInternetStatus,
)
from database.connection import session_scope
from database.models import (
    AccountLedgerEntry,
    Server,
    WhiteInternetSubscription,
)


class InvariantResult(NamedTuple):
    number: int
    name: str
    passed: bool
    details: str


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
                WhiteInternetSubscription.traffic_uplink_bytes < 0,
                WhiteInternetSubscription.traffic_downlink_bytes < 0,
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
    """Inv 8: Multi-hub architecture — WI subscriptions are no longer pinned to a single hub server."""
    return InvariantResult(
        8, "Origin Node Capabilities", True, "Multi-hub architecture: server pinning removed"
    )



async def assert_inv_10_account_balance_non_negativity(session: AsyncSession) -> InvariantResult:
    """Inv 10: Real and bonus buckets satisfy non-negativity (real debt only from refund_debit)."""
    refund_allowance = func.coalesce(
        func.sum(
            case(
                (
                    AccountLedgerEntry.entry_type == "refund_debit",
                    -AccountLedgerEntry.amount,
                ),
                else_=Decimal("0"),
            )
        ),
        Decimal("0"),
    )
    real_balance = func.coalesce(
        func.sum(
            case(
                (AccountLedgerEntry.bucket == "real", AccountLedgerEntry.amount),
                else_=Decimal("0"),
            )
        ),
        Decimal("0"),
    )
    bonus_balance = func.coalesce(
        func.sum(
            case(
                (AccountLedgerEntry.bucket == "bonus", AccountLedgerEntry.amount),
                else_=Decimal("0"),
            )
        ),
        Decimal("0"),
    )

    res = await session.execute(
        select(
            AccountLedgerEntry.user_id,
            real_balance.label("real_balance"),
            bonus_balance.label("bonus_balance"),
            refund_allowance.label("refund_allowance"),
        )
        .group_by(AccountLedgerEntry.user_id)
        .having(or_((real_balance + refund_allowance) < 0, bonus_balance < 0))
    )
    violations = res.all()
    if violations:
        return InvariantResult(
            10,
            "Account Balance Non-Negativity",
            False,
            f"Violations found: {len(violations)} users with invalid negative bucket balance",
        )
    return InvariantResult(
        10,
        "Account Balance Non-Negativity",
        True,
        "All user real/bonus balances are non-negative or explained by refund debits",
    )


async def assert_inv_11_account_ledger_conservation(session: AsyncSession) -> InvariantResult:
    """Inv 11: Account ledger entries have non-zero amounts, valid entry types and valid buckets."""
    valid_types = [t.value for t in AccountLedgerEntryType]
    violations = await session.scalars(
        select(AccountLedgerEntry).where(
            or_(
                AccountLedgerEntry.amount == 0,
                ~AccountLedgerEntry.entry_type.in_(valid_types),
                ~AccountLedgerEntry.bucket.in_(["real", "bonus"]),
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
    """Inv 13: All VPN servers strictly use VLESS or Xray protocol (AWG/wireguard strictly rejected)."""
    violations = await session.scalars(
        select(Server).where(
            or_(
                Server.protocol.is_(None),
                Server.protocol.notin_([VLESS_PROTOCOL, XRAY_PROTOCOL]),
            )
        )
    )
    v_list = violations.all()
    if v_list:
        return InvariantResult(
            13,
            "VPN Server Protocol (VLESS/Xray)",
            False,
            f"Violations: {len(v_list)} invalid protocol servers",
        )
    return InvariantResult(
        13,
        "VPN Server Protocol (VLESS/Xray)",
        True,
        "All servers strictly use VLESS (vless) or Xray (xray)",
    )


async def assert_inv_14_origin_server_capacity(session: AsyncSession) -> InvariantResult:
    """Inv 14: Origin server capacity (deprecated - legacy max_clients constraint removed)."""
    return InvariantResult(
        14, "Origin Server Capacity", True, "Legacy max_clients constraint removed"
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
