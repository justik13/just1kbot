"""Auto-seeding middleware for the local simulation testbed."""
from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from decimal import Decimal

from aiogram.types import Update
from sqlalchemy import select

from database.connection import session_scope
from database.models import (
    AccountLedgerEntry,
    Payment,
    Server,
    Tariff,
    TariffQuote,
    TariffVersion,
    User,
    VPNProfile,
)
from scripts.simulate.service_mocks import generate_mock_amnezia_vpn_uri
from utils.datetime_helpers import now_utc

# --- 4. DYNAMIC USER AUTO-SEEDING MIDDLEWARE ---

class SimulationAutoSeedMiddleware:
    """Automatically seeds newly connected Telegram users with realistic account state."""

    def __init__(
        self,
        real_balance: Decimal = Decimal(350),
        bonus_balance: Decimal = Decimal(150),
        enabled: bool = True,
    ):
        self.real_balance = real_balance
        self.bonus_balance = bonus_balance
        self.enabled = enabled

    async def __call__(self, handler, event: Update, data: dict):
        if not self.enabled:
            return await handler(event, data)

        user = getattr(event, "from_user", None)
        if not user:
            return await handler(event, data)

        # In simulation mode, dynamically grant admin rights to connecting tester
        from config.settings import get_settings
        sim_settings = get_settings()
        if user.id not in sim_settings.ADMIN_IDS:
            sim_settings.ADMIN_IDS.append(user.id)

        async with session_scope() as session:
            db_user = await session.scalar(
                select(User).where(User.telegram_id == user.id)
            )
            if not db_user:
                    tariff = await session.scalar(
                        select(Tariff)
                        .where(
                            Tariff.is_active.is_(True),
                            Tariff.service_type == "awg",
                            Tariff.duration_days == 30,
                        )
                        .order_by(Tariff.device_limit.asc())
                        .limit(1)
                    )
                    tariff_id = tariff.id if tariff else None
                    device_limit = getattr(tariff, "device_limit", 2)
                    tv = await session.scalar(
                        select(TariffVersion).where(TariffVersion.tariff_id == tariff_id).limit(1)
                    ) if tariff_id else None
                    tv_id = tv.id if tv else None

                    server = await session.scalar(
                        select(Server).where(Server.is_active.is_(True)).order_by(Server.id.asc()).limit(1)
                    )
                    server_id = server.id if server else 1

                    # Create user record
                    db_user = User(
                        telegram_id=user.id,
                        username=user.username or f"user_{user.id}",
                        first_name=user.first_name or "Tester",
                        device_limit=device_limit,
                        current_tariff_id=tariff_id,
                        subscription_end=now_utc() + timedelta(days=28),
                        created_at=now_utc() - timedelta(days=2),
                    )
                    session.add(db_user)
                    await session.flush()

                    # Seed initial payment & ledger entries
                    seed_pay = Payment(
                        user_id=db_user.id,
                        amount=self.real_balance,
                        currency="RUB",
                        public_order_id=f"order_{uuid.uuid4().hex[:8]}",
                        provider_idempotency_key=f"idem_{uuid.uuid4().hex[:12]}",
                        provider_status="succeeded",
                        fulfillment_status="succeeded",
                        reconciliation_status="ok",
                        checkout_status="active",
                        ui_visible=True,
                        created_at=now_utc() - timedelta(days=2),
                        paid_at=now_utc() - timedelta(days=2),
                        credited_at=now_utc() - timedelta(days=2),
                        credit_notified_at=now_utc() - timedelta(days=2),
                    )
                    session.add(seed_pay)
                    await session.flush()

                    ts = int(now_utc().timestamp() * 1000)
                    entry_real = AccountLedgerEntry(
                        id=ts + 1,
                        user_id=db_user.id,
                        amount=self.real_balance,
                        currency="RUB",
                        entry_type="payment_credit",
                        payment_id=seed_pay.id,
                        idempotency_key=f"seed_real_{uuid.uuid4().hex}",
                        metadata_={"note": "Initial simulation balance"},
                        created_at=now_utc() - timedelta(days=2),
                    )
                    entry_bonus = AccountLedgerEntry(
                        id=ts + 2,
                        user_id=db_user.id,
                        amount=self.bonus_balance,
                        currency="RUB",
                        entry_type="admin_adjustment",
                        idempotency_key=f"seed_bonus_{uuid.uuid4().hex}",
                        metadata_={
                            "source_type": "referral_referrer_bonus",
                            "reason": "welcome_bonus",
                        },
                        created_at=now_utc() - timedelta(days=2),
                    )

                    # Initial quote, entitlement and paid value ledger
                    init_quote = TariffQuote(
                        public_id=uuid.uuid4(),
                        user_id=db_user.id,
                        target_tariff_version_id=tv_id,
                        operation_type="purchase",
                        current_paid_hours=0,
                        current_paid_value_rub=Decimal(0),
                        bonus_hours=0,
                        amount_due_rub=Decimal(180),
                        resulting_paid_hours=720,
                        resulting_paid_value_rub=Decimal(180),
                        resulting_bonus_hours=0,
                        rounding_loss_hours=Decimal(0),
                        rounding_loss_value_rub=Decimal(0),
                        status="consumed",
                        consumed_at=now_utc() - timedelta(days=2),
                        purchase_notified_at=now_utc() - timedelta(days=2),
                        expires_at=now_utc(),
                        created_at=now_utc() - timedelta(days=2),
                    )
                    session.add(init_quote)
                    await session.flush()

                    session.add_all([entry_real, entry_bonus])

                    # Create 1 Active Device (iPhone)
                    prof = VPNProfile(
                        user_id=db_user.id,
                        server_id=server_id,
                        device_name="iPhone 16 Pro",
                        client_name="iPhone 16 Pro",
                        peer_id="peer_sim_nl_iphone",
                        raw_config=generate_mock_amnezia_vpn_uri(
                            "iPhone 16 Pro", "peer_sim_nl_iphone"
                        ),
                        provisioning_status="active",
                        desired_version=1,
                        is_active=True,
                        created_at=now_utc(),
                    )
                    session.add(prof)

                    # Seed 3 Mock Referrals for this user
                    ref1 = User(
                        telegram_id=user.id + 101,
                        username=f"friend_dmitry_{user.id}",
                        first_name="Дмитрий",
                        referred_by=user.id,
                        created_at=now_utc() - timedelta(days=10),
                    )
                    ref2 = User(
                        telegram_id=user.id + 102,
                        username=f"friend_elena_{user.id}",
                        first_name="Елена",
                        referred_by=user.id,
                        created_at=now_utc() - timedelta(days=5),
                    )
                    ref3 = User(
                        telegram_id=user.id + 103,
                        username=f"friend_sergey_{user.id}",
                        first_name="Сергей",
                        referred_by=user.id,
                        created_at=now_utc() - timedelta(days=2),
                    )
                    session.add_all([ref1, ref2, ref3])

                    logging.getLogger("simulation.seed").info(
                        "✨ [AUTO-SEED] Initialized user @%s (ID %s) with %s₽ real + %s₽ bonus + 1 active device + 3 referrals.",
                        db_user.username,
                        user.id,
                        self.real_balance,
                        self.bonus_balance,
                    )

        return await handler(event, data)
