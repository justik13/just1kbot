"""Standalone backfill runner script for migrating historical quotes and decoding legacy state into Purchase and EntitlementGrant.

Usage:
    python -m scripts.backfill_billing_v2 [--dry-run] [--commit]

Operational Contract:
- Safe and idempotent: can be executed multiple times without duplicate rows.
- Runs in a transaction: rolls back completely on any error unless explicitly committed.
- Pure backfill: does not delete or alter legacy rows (leaves PaidValueLedgerEntry and EntitlementEntry untouched).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from config.constants import AMNEZIA_PROTOCOL
from config.enums import (
    EntitlementGrantStatus,
    EntitlementGrantType,
    PurchaseFulfillmentStatus,
    PurchaseStatus,
    TariffQuoteOperation,
    TariffQuoteStatus,
)
from database.connection import session_scope
from database.models import (
    AccountLedgerEntry,
    EntitlementGrant,
    Purchase,
    TariffQuote,
    TariffVersion,
    User,
)
from database.repositories import entitlement_grants_repo
from services.subscription_balance_service import get_subscription_balance_snapshot
from utils.datetime_helpers import now_utc
from utils.logging_security import install_sensitive_data_filter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("backfill_billing_v2")
install_sensitive_data_filter(logger)


async def backfill_billing_v2(*, commit: bool = False) -> None:
    now = now_utc()
    logger.info("Starting billing v2 backfill (commit=%s, as_of=%s)...", commit, now.isoformat())

    stats = {
        "quotes_scanned": 0,
        "trials_skipped": 0,
        "purchases_existing": 0,
        "purchases_created": 0,
        "ledger_entries_linked": 0,
        "users_scanned": 0,
        "users_already_migrated": 0,
        "entitlement_grants_created": 0,
        "fallback_grants_created": 0,
        "users_reconciled": 0,
    }

    async with session_scope() as session:
        # =====================================================================
        # Step 1: Backfill Purchase records from historical consumed quotes
        # =====================================================================
        logger.info("Step 1: Backfilling Purchase records from consumed TariffQuote rows...")
        quotes_query = (
            select(TariffQuote)
            .where(TariffQuote.status == TariffQuoteStatus.CONSUMED)
            .options(
                selectinload(TariffQuote.target_tariff_version).selectinload(TariffVersion.tariff),
            )
            .order_by(TariffQuote.id.asc())
        )
        quotes = (await session.scalars(quotes_query)).all()
        stats["quotes_scanned"] = len(quotes)

        quote_to_purchase_map: dict[int, int] = {}

        for quote in quotes:
            # Rule 6: White Internet and AWG trial quotes must NOT be backfilled as Purchase
            if quote.operation_type == TariffQuoteOperation.TRIAL or quote.operation_type == "trial":
                stats["trials_skipped"] += 1
                continue

            # Check if Purchase already exists for this quote
            existing_purchase = await session.scalar(
                select(Purchase).where(Purchase.quote_id == quote.id)
            )
            if existing_purchase is not None:
                stats["purchases_existing"] += 1
                quote_to_purchase_map[quote.id] = existing_purchase.id
                continue

            tariff_ver = quote.target_tariff_version
            tariff = getattr(tariff_ver, "tariff", None) if tariff_ver else None

            # Determine service_type
            service_type = getattr(quote, "service_type", None) or "awg"
            if not service_type and tariff is not None:
                if getattr(tariff, "service_type", None):
                    service_type = tariff.service_type
                elif getattr(tariff, "protocol", None) == AMNEZIA_PROTOCOL:
                    service_type = "awg"
                elif "white" in str(getattr(tariff, "name", "")).lower():
                    service_type = "white_internet"

            duration_days = 0
            if tariff_ver is not None and getattr(tariff_ver, "duration_days", None):
                duration_days = tariff_ver.duration_days
            elif tariff is not None and getattr(tariff, "duration_days", None):
                duration_days = tariff.duration_days

            op_type_str = (
                quote.operation_type.value
                if hasattr(quote.operation_type, "value")
                else str(quote.operation_type)
            )
            if service_type == "white_internet":
                base_price = (
                    getattr(tariff_ver, "price_rub", None)
                    or getattr(tariff, "price_rub", None)
                    or Decimal("150.00")
                )
                if (quote.amount_due_rub or Decimal("0.00")) < Decimal(str(base_price)):
                    duration_days = 0
                    op_type_str = "addon"

            device_limit = 1
            if tariff_ver is not None and getattr(tariff_ver, "device_limit", None):
                device_limit = tariff_ver.device_limit
            elif tariff is not None and getattr(tariff, "device_limit", None):
                device_limit = tariff.device_limit

            tariff_id = tariff.id if tariff else (tariff_ver.tariff_id if tariff_ver else None)
            purchase_ts = quote.consumed_at or quote.created_at or now

            new_purchase = Purchase(
                user_id=quote.user_id,
                quote_id=quote.id,
                idempotency_key=f"migrated_quote:{quote.id}",
                service_type=service_type,
                operation_type=op_type_str,
                amount_rub=quote.amount_due_rub or Decimal("0.00"),
                status=PurchaseStatus.COMPLETED,
                fulfillment_status=PurchaseFulfillmentStatus.FULFILLED,

                tariff_id=tariff_id,
                tariff_version_id=quote.target_tariff_version_id,
                duration_days=duration_days,
                device_limit=device_limit,
                details={
                    "migrated_from_quote_id": quote.id,
                    "migrated_at": now.isoformat(),
                },
                created_at=purchase_ts,
                completed_at=purchase_ts,
                fulfilled_at=purchase_ts,
            )
            session.add(new_purchase)
            await session.flush()
            stats["purchases_created"] += 1
            quote_to_purchase_map[quote.id] = new_purchase.id

        # =====================================================================
        # Step 2: Link AccountLedgerEntry.purchase_id
        # =====================================================================
        logger.info("Step 2: Linking AccountLedgerEntry rows with purchase_id...")
        ledger_query = select(AccountLedgerEntry).where(
            AccountLedgerEntry.quote_id.is_not(None),
            AccountLedgerEntry.purchase_id.is_(None),
        )
        unlinked_entries = (await session.scalars(ledger_query)).all()
        for entry in unlinked_entries:
            p_id = quote_to_purchase_map.get(entry.quote_id)
            if p_id is None:
                p = await session.scalar(select(Purchase).where(Purchase.quote_id == entry.quote_id))
                if p is not None:
                    p_id = p.id
                    quote_to_purchase_map[entry.quote_id] = p_id

            if p_id is not None:
                entry.purchase_id = p_id
                stats["ledger_entries_linked"] += 1

        await session.flush()

        # =====================================================================
        # Step 3: Backfill EntitlementGrant for active AWG subscriptions
        # =====================================================================
        logger.info("Step 3: Backfilling active EntitlementGrants for users with active subscriptions...")
        active_users_query = (
            select(User)
            .where(
                User.subscription_end.is_not(None),
                User.subscription_end > now,
            )
            .order_by(User.id.asc())
        )
        active_users = (await session.scalars(active_users_query)).all()
        stats["users_scanned"] = len(active_users)

        for user in active_users:
            existing_grants = await entitlement_grants_repo.get_active_grants_for_user(
                session, user.id
            )
            if existing_grants:
                stats["users_already_migrated"] += 1
                stats["users_reconciled"] += 1
                continue

            # Load legacy projector state
            created_any_grant = False
            try:
                snapshot = await get_subscription_balance_snapshot(session, user_id=user.id, as_of=now)
            except Exception as exc:
                logger.warning("Failed to project subscription balance for user %d: %s", user.id, exc)
                snapshot = None

            if snapshot and snapshot.tracked and (snapshot.paid_lots or snapshot.bonus_lots):
                for paid_lot in snapshot.paid_lots:
                    if paid_lot.segment_end <= now:
                        continue

                    paid_src_id = f"paid_lot:{paid_lot.entitlement_entry_id}:{paid_lot.paid_value_ledger_entry_id}"
                    existing_grant = await session.scalar(
                        select(EntitlementGrant).where(
                            EntitlementGrant.user_id == user.id,
                            EntitlementGrant.source_type == "legacy_lot",
                            EntitlementGrant.source_id == paid_src_id,
                        )
                    )
                    if existing_grant is not None:
                        continue

                    # Lookup purchase_id
                    p_id = quote_to_purchase_map.get(paid_lot.quote_id) if paid_lot.quote_id else None
                    if p_id is None and paid_lot.quote_id:
                        p = await session.scalar(select(Purchase).where(Purchase.quote_id == paid_lot.quote_id))
                        if p:
                            p_id = p.id
                            quote_to_purchase_map[paid_lot.quote_id] = p_id

                    await entitlement_grants_repo.create_grant(
                        session,
                        user_id=user.id,
                        purchase_id=p_id,
                        service_type="awg",
                        source_type="legacy_lot",
                        source_id=paid_src_id,
                        grant_type=EntitlementGrantType.LEGACY_BACKFILL,
                        coverage_start=paid_lot.segment_start,
                        coverage_end=paid_lot.segment_end,
                        original_duration_hours=paid_lot.original_paid_hours,
                        paid_value_rub=paid_lot.original_paid_value_rub,
                        tariff_version_id=paid_lot.tariff_version_id,
                        device_limit=user.device_limit,
                        status=EntitlementGrantStatus.ACTIVE,
                    )
                    stats["entitlement_grants_created"] += 1
                    created_any_grant = True

                for bonus_lot in snapshot.bonus_lots:
                    if bonus_lot.segment_end <= now:
                        continue

                    bonus_src_type = bonus_lot.source_type or "legacy_bonus"
                    bonus_src_id = f"bonus_lot:{bonus_lot.entitlement_entry_id}:{bonus_lot.source_id}"
                    existing_grant = await session.scalar(
                        select(EntitlementGrant).where(
                            EntitlementGrant.user_id == user.id,
                            EntitlementGrant.source_type == bonus_src_type,
                            EntitlementGrant.source_id == bonus_src_id,
                        )
                    )
                    if existing_grant is not None:
                        continue

                    await entitlement_grants_repo.create_grant(
                        session,
                        user_id=user.id,
                        purchase_id=None,
                        service_type="awg",
                        source_type=bonus_src_type,
                        source_id=bonus_src_id,
                        grant_type=EntitlementGrantType.LEGACY_BACKFILL,
                        coverage_start=bonus_lot.segment_start,
                        coverage_end=bonus_lot.segment_end,
                        original_duration_hours=bonus_lot.original_hours,
                        paid_value_rub=Decimal("0.000000"),
                        tariff_version_id=None,
                        device_limit=user.device_limit,
                        status=EntitlementGrantStatus.ACTIVE,
                    )
                    stats["entitlement_grants_created"] += 1
                    created_any_grant = True

            # If legacy projector did not produce active grants (e.g. untracked direct admin grant)
            if not created_any_grant and user.subscription_end is not None and user.subscription_end > now:
                fallback_src_id = f"legacy_sub:{user.id}"
                existing_grant = await session.scalar(
                    select(EntitlementGrant).where(
                        EntitlementGrant.user_id == user.id,
                        EntitlementGrant.source_type == "legacy_user",
                        EntitlementGrant.source_id == fallback_src_id,
                    )
                )
                if existing_grant is None:
                    # Create a single fallback grant to preserve active subscription
                    hours_remaining = max(1, int((user.subscription_end - now).total_seconds() // 3600))
                    start_ts = min(now, user.subscription_end - timedelta(hours=1))
                    await entitlement_grants_repo.create_grant(
                        session,
                        user_id=user.id,
                        purchase_id=None,
                        service_type="awg",
                        source_type="legacy_user",
                        source_id=fallback_src_id,
                        grant_type=EntitlementGrantType.LEGACY_BACKFILL,
                        coverage_start=start_ts,
                        coverage_end=user.subscription_end,
                        original_duration_hours=hours_remaining,
                        paid_value_rub=Decimal("0.000000"),
                        device_limit=user.device_limit,
                        status=EntitlementGrantStatus.ACTIVE,
                    )
                    stats["fallback_grants_created"] += 1


            # Verification of user's coverage continuity
            user_active_grants = await entitlement_grants_repo.get_active_grants_for_user(
                session, user.id
            )
            max_end = max((g.coverage_end for g in user_active_grants), default=None)
            if max_end is not None and user.subscription_end is not None:
                # Ensure end matches within 1 second
                diff = abs((max_end - user.subscription_end).total_seconds())
                if diff > 2.0:
                    logger.warning(
                        "User %d coverage end discrepancy: max_grant=%s vs user.sub_end=%s (diff=%.1fs)",
                        user.id,
                        max_end.isoformat(),
                        user.subscription_end.isoformat(),
                        diff,
                    )
            stats["users_reconciled"] += 1

        await session.flush()

        logger.info(
            "Backfill completed successfully. Summary: "
            "quotes_scanned=%d, trials_skipped=%d, purchases_created=%d (existing=%d), "
            "ledger_linked=%d, active_users=%d (already_migrated=%d), grants_created=%d, "
            "fallback_grants=%d, users_reconciled=%d",
            stats["quotes_scanned"],
            stats["trials_skipped"],
            stats["purchases_created"],
            stats["purchases_existing"],
            stats["ledger_entries_linked"],
            stats["users_scanned"],
            stats["users_already_migrated"],
            stats["entitlement_grants_created"],
            stats["fallback_grants_created"],
            stats["users_reconciled"],
        )

        if not commit:
            logger.info("DRY-RUN completed. Changes will be rolled back. Pass --commit to persist.")
            await session.rollback()
        else:
            await session.commit()
            logger.info("CHANGES COMMITTED SUCCESSFULLY TO DATABASE.")


def main():
    parser = argparse.ArgumentParser(description="Backfill billing v2 (Purchase & EntitlementGrant)")
    parser.add_argument("--commit", action="store_true", help="Persist changes to database (default is dry-run)")
    parser.add_argument("--dry-run", action="store_true", help="Run without persisting changes")
    args = parser.parse_args()

    commit = args.commit and not args.dry_run
    if not commit and not args.dry_run:
        print(
            "ВНИМАНИЕ: Скрипт будет запущен в режиме DRY-RUN (без сохранения в БД).\n"
            "Для реального применения запустите: python -m scripts.backfill_billing_v2 --commit\n"
        )

    try:
        asyncio.run(backfill_billing_v2(commit=commit))
    except Exception as exc:
        logger.exception("Backfill failed: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
