"""Unit tests for White Internet tariff model, device tier upgrade, admin restrictions, and quota invariants."""

import ast
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from bot import texts
from config.enums import (
    ServerHealthState,
    ServerLifecycleStatus,
    WhiteInternetStatus,
)
from database.models import Server, Tariff, TariffVersion, User, WhiteInternetSubscription
from database.repositories import white_internet_repo
from database.repositories.account_ledger_repo import (
    AccountBalanceSnapshot,
    InsufficientAccountBalanceError,
)
from database.repositories.white_internet_repo import (
    WhiteInternetResetCooldownError,
)
from services.white_internet_service import (
    WhiteInternetService,
    get_white_internet_tier_price,
)


class TestWhiteInternetTierModel(unittest.TestCase):
    """Test deterministic tier pricing helper."""

    def test_tier_price_calculation(self):
        # 1 device: 250 RUB
        self.assertEqual(get_white_internet_tier_price(1), Decimal("250.00"))
        # 2 devices: 450 RUB
        self.assertEqual(get_white_internet_tier_price(2), Decimal("450.00"))
        # 3 devices: 650 RUB
        self.assertEqual(get_white_internet_tier_price(3), Decimal("650.00"))
        # Boundaries: <= 0 defaults to 1 device (250 RUB)
        self.assertEqual(get_white_internet_tier_price(0), Decimal("250.00"))
        self.assertEqual(get_white_internet_tier_price(-1), Decimal("250.00"))
        # > 3 devices capped at max 3 (650 RUB)
        self.assertEqual(get_white_internet_tier_price(4), Decimal("650.00"))
        self.assertEqual(get_white_internet_tier_price(10), Decimal("650.00"))

    def test_tier_price_calculation_with_dynamic_base_price(self):
        # Dynamic base price 300 RUB
        self.assertEqual(get_white_internet_tier_price(1, base_price=Decimal("300.00")), Decimal("300.00"))
        self.assertEqual(get_white_internet_tier_price(2, base_price=Decimal("300.00")), Decimal("500.00"))
        self.assertEqual(get_white_internet_tier_price(3, base_price=Decimal("300.00")), Decimal("700.00"))


class TestWhiteInternetDeviceSlotPurchase(unittest.IsolatedAsyncioTestCase):
    """Test device slot purchase (tier upgrade) business logic and ledger invariants."""

    def setUp(self):
        self.now = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)
        self.user = User(id=42, telegram_id=111222, is_banned=False, is_deleted=False)
        self.sub = WhiteInternetSubscription(
            id=1,
            user_id=42,
            origin_node_id=10,
            token="test-token-12345",
            uuid="a2b9d4e1-73c5-4812-b964-f3e7b85a1902",
            status=WhiteInternetStatus.ACTIVE,
            started_at=self.now - timedelta(days=5),
            expires_at=self.now + timedelta(days=25),
            device_limit=1,
            base_traffic_bytes=50 * 1024**3,
            extra_traffic_bytes=0,
            traffic_used_bytes=10 * 1024**3,
            desired_version=1,
            actual_version=1,
        )
        self.server = Server(
            id=10,
            protocol="xray",
            is_active=True,
            health_state=ServerHealthState.ONLINE,
            lifecycle_status=ServerLifecycleStatus.ACTIVE,
            capabilities=["xray_origin"],
            extra_data={"relays": [{"code": "de", "name": "DE"}]},
            api_url="http://node10.test:8080",
            api_key="secret",
            xray_instance_epoch="ep1",
        )

    async def test_purchase_device_slot_unauthorized_actor_rejected(self):
        mock_session = AsyncMock()
        with patch("services.white_internet_service.is_admin", return_value=False), \
             patch("services.white_internet_service.lock_checkout_user", return_value=self.user):
            ok, msg, sub = await WhiteInternetService.purchase_device_slot(
                mock_session, user_id=42, actor_telegram_id=999888
            )
            self.assertFalse(ok)
            self.assertEqual(msg, texts.WL_ADMIN_ONLY_ALERT)
            self.assertIsNone(sub)

    async def test_purchase_device_slot_trial_rejected(self):
        mock_session = AsyncMock()
        trial_sub = WhiteInternetSubscription(
            id=1,
            user_id=42,
            origin_node_id=10,
            status=WhiteInternetStatus.ACTIVE,
            device_limit=1,
            is_trial=True,
            base_traffic_bytes=5 * 1024**3,
            traffic_used_bytes=0,
        )
        with patch("services.white_internet_service.is_admin", return_value=False), \
             patch("services.white_internet_service.lock_checkout_user", return_value=self.user), \
             patch("database.repositories.white_internet_repo.get_subscription_by_user_id", return_value=trial_sub):
            ok, msg, sub = await WhiteInternetService.purchase_device_slot(
                mock_session, user_id=42, actor_telegram_id=self.user.telegram_id
            )
            self.assertFalse(ok)
            self.assertEqual(msg, texts.WL_TRIAL_CANNOT_ADD_DEVICE)
            self.assertIsNone(sub)

    async def test_purchase_device_slot_regular_user_success(self):
        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_session.scalar = AsyncMock(return_value=self.server)

        tariff = Tariff(id=1, service_type="white_internet", duration_days=30, price_rub=250)
        tariff_version = TariffVersion(
            id=1,
            tariff_id=1,
            version_number=1,
            name_snapshot="Белый Интернет 50 ГБ",
            service_type="white_internet",
            device_limit=1,
            price_rub=Decimal("250.00"),
            duration_hours=720,
            base_quota_bytes=50 * 1024**3,
        )

        with patch("services.white_internet_service.is_admin", return_value=False), \
             patch("services.white_internet_service.lock_checkout_user", return_value=self.user), \
             patch("database.repositories.white_internet_repo.get_subscription_by_user_id", return_value=self.sub), \
             patch("services.white_internet_service.get_account_balance", return_value=AccountBalanceSnapshot(accounting_position=Decimal("1000.00"), available=Decimal("1000.00"), reserved=Decimal("0"), debt=Decimal("0"))), \
             patch("services.white_internet_service.create_purchase_debit", new_callable=AsyncMock) as mock_debit, \
             patch("services.white_internet_service.WhiteInternetService.get_or_create_white_internet_tariff", return_value=tariff), \
             patch("services.white_internet_service.get_or_create_current_version", return_value=tariff_version), \
             patch("database.repositories.white_internet_repo.add_device_slot_atomic") as mock_add_slot:

            updated_sub = WhiteInternetSubscription(
                id=1,
                user_id=42,
                device_limit=2,
                base_traffic_bytes=50 * 1024**3,
                extra_traffic_bytes=50 * 1024**3,
            )
            mock_add_slot.return_value = updated_sub

            ok, msg, result = await WhiteInternetService.purchase_device_slot(
                mock_session, user_id=42, actor_telegram_id=self.user.telegram_id
            )

            self.assertTrue(ok)
            self.assertEqual(result.device_limit, 2)
            mock_debit.assert_awaited_once()
            debit_call_args = mock_debit.await_args[1]
            self.assertEqual(debit_call_args["amount"], Decimal("200.00"))

    async def test_purchase_device_slot_admin_success(self):
        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_session.scalar = AsyncMock(return_value=self.server)

        tariff = Tariff(id=1, service_type="white_internet", duration_days=30, price_rub=250)
        tariff_version = TariffVersion(
            id=1,
            tariff_id=1,
            version_number=1,
            name_snapshot="Белый Интернет 50 ГБ",
            service_type="white_internet",
            device_limit=1,
            price_rub=Decimal("250.00"),
            duration_hours=720,
            base_quota_bytes=50 * 1024**3,
        )

        with patch("services.white_internet_service.is_admin", return_value=True), \
             patch("services.white_internet_service.lock_checkout_user", return_value=self.user), \
             patch("database.repositories.white_internet_repo.get_subscription_by_user_id", return_value=self.sub), \
             patch("services.white_internet_service.get_account_balance", return_value=AccountBalanceSnapshot(accounting_position=Decimal("1000.00"), available=Decimal("1000.00"), reserved=Decimal("0"), debt=Decimal("0"))), \
             patch("services.white_internet_service.create_purchase_debit", new_callable=AsyncMock) as mock_debit, \
             patch("services.white_internet_service.WhiteInternetService.get_or_create_white_internet_tariff", return_value=tariff), \
             patch("services.white_internet_service.get_or_create_current_version", return_value=tariff_version), \
             patch("database.repositories.white_internet_repo.add_device_slot_atomic") as mock_add_slot:

            updated_sub = WhiteInternetSubscription(
                id=1,
                user_id=42,
                device_limit=2,
                base_traffic_bytes=50 * 1024**3,
                extra_traffic_bytes=50 * 1024**3,
            )
            mock_add_slot.return_value = updated_sub

            ok, msg, result = await WhiteInternetService.purchase_device_slot(
                mock_session, user_id=42, actor_telegram_id=999999
            )

            self.assertTrue(ok)
            self.assertEqual(result.device_limit, 2)
            # Verify debit called with exactly 200 RUB
            mock_debit.assert_awaited_once()
            debit_call_args = mock_debit.await_args[1]
            self.assertEqual(debit_call_args["amount"], Decimal("200.00"))
            # Verify add_device_slot_atomic called with 50 GiB
            mock_add_slot.assert_awaited_once_with(
                mock_session,
                subscription_id=1,
                extra_bytes=50 * 1024**3,
            )

    async def test_purchase_device_slot_max_limit_rejected(self):
        mock_session = AsyncMock()
        self.sub.device_limit = 3

        with patch("services.white_internet_service.is_admin", return_value=True), \
             patch("services.white_internet_service.lock_checkout_user", return_value=self.user), \
             patch("database.repositories.white_internet_repo.get_subscription_by_user_id", return_value=self.sub):

            ok, msg, result = await WhiteInternetService.purchase_device_slot(
                mock_session, user_id=42, actor_telegram_id=999999
            )
            self.assertFalse(ok)
            self.assertEqual(msg, texts.WL_DEVICE_LIMIT_MAX_REACHED)
            self.assertIsNone(result)

    async def test_purchase_device_slot_quota_cap_pre_debit_validation(self):
        """Pre-debit validation must reject before debit if quota exceeds 150 GiB."""
        mock_session = AsyncMock()
        # Already 50 GiB base + 60 GiB extra = 110 GiB. Adding 50 GiB extra = 160 GiB > 150 GiB cap!
        self.sub.base_traffic_bytes = 50 * 1024**3
        self.sub.extra_traffic_bytes = 60 * 1024**3

        with patch("services.white_internet_service.is_admin", return_value=True), \
             patch("services.white_internet_service.lock_checkout_user", return_value=self.user), \
             patch("database.repositories.white_internet_repo.get_subscription_by_user_id", return_value=self.sub), \
             patch("database.repositories.white_internet_repo.get_available_quota_bytes", return_value=110 * 1024**3), \
             patch("database.repositories.account_ledger_repo.create_purchase_debit", new_callable=AsyncMock) as mock_debit:

            ok, msg, result = await WhiteInternetService.purchase_device_slot(
                mock_session, user_id=42, actor_telegram_id=999999
            )
            self.assertFalse(ok)
            self.assertIn("150 ГБ", msg)
            # Pre-debit validation invariant: financial debit must NOT be touched
            mock_debit.assert_not_called()

    async def test_purchase_device_slot_insufficient_balance(self):
        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_session.scalar = AsyncMock(return_value=self.server)

        tariff = Tariff(id=1, service_type="white_internet", duration_days=30, price_rub=250)
        tariff_version = TariffVersion(
            id=1,
            tariff_id=1,
            version_number=1,
            name_snapshot="Белый Интернет 50 ГБ",
            service_type="white_internet",
            device_limit=1,
            price_rub=Decimal("250.00"),
            duration_hours=720,
            base_quota_bytes=50 * 1024**3,
        )

        with patch("services.white_internet_service.is_admin", return_value=True), \
             patch("services.white_internet_service.lock_checkout_user", return_value=self.user), \
             patch("database.repositories.white_internet_repo.get_subscription_by_user_id", return_value=self.sub), \
             patch("services.white_internet_service.get_account_balance", return_value=AccountBalanceSnapshot(accounting_position=Decimal("50.00"), available=Decimal("50.00"), reserved=Decimal("0"), debt=Decimal("0"))), \
             patch("services.white_internet_service.create_purchase_debit", side_effect=InsufficientAccountBalanceError("Insufficient funds")), \
             patch("services.white_internet_service.WhiteInternetService.get_or_create_white_internet_tariff", return_value=tariff), \
             patch("services.white_internet_service.get_or_create_current_version", return_value=tariff_version):

            ok, msg, result = await WhiteInternetService.purchase_device_slot(
                mock_session, user_id=42, actor_telegram_id=999999
            )
            self.assertFalse(ok)
            self.assertIn("Недостаточно средств", msg)
            self.assertIsNone(result)


class TestWhiteInternetRenewalTierInvariants(unittest.IsolatedAsyncioTestCase):
    """Test renewal with preserved device limits, dynamic pricing, and quota caps."""

    def setUp(self):
        self.now = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)
        self.user = User(id=42, telegram_id=111222, is_banned=False, is_deleted=False)
        self.server = Server(
            id=10,
            protocol="xray",
            is_active=True,
            health_state=ServerHealthState.ONLINE,
            lifecycle_status=ServerLifecycleStatus.ACTIVE,
            capabilities=["xray_origin"],
            extra_data={"relays": [{"code": "de", "name": "DE"}]},
            api_url="http://node10.test:8080",
            api_key="secret",
            xray_instance_epoch="ep1",
        )

    async def test_renew_subscription_dynamic_tier_pricing_limit_2(self):
        """Subscription with device_limit=2 must renew for 450 RUB with 100 GiB base."""
        sub = WhiteInternetSubscription(
            id=1,
            user_id=42,
            origin_node_id=10,
            status=WhiteInternetStatus.ACTIVE,
            device_limit=2,
            expires_at=self.now + timedelta(days=5),
            base_traffic_bytes=50 * 1024**3,
            extra_traffic_bytes=50 * 1024**3,
        )

        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_session.scalar = AsyncMock(return_value=self.server)

        tariff = Tariff(id=1, service_type="white_internet", duration_days=30, price_rub=250)
        tariff_version = TariffVersion(
            id=1,
            tariff_id=1,
            version_number=1,
            name_snapshot="Белый Интернет 50 ГБ",
            service_type="white_internet",
            device_limit=1,
            price_rub=Decimal("250.00"),
            duration_hours=720,
            base_quota_bytes=50 * 1024**3,
        )

        with patch("services.white_internet_service.lock_checkout_user", return_value=self.user), \
             patch("database.repositories.white_internet_repo.get_subscription_by_user_id", return_value=sub), \
             patch("services.white_internet_service.get_account_balance", return_value=AccountBalanceSnapshot(accounting_position=Decimal("1000.00"), available=Decimal("1000.00"), reserved=Decimal("0"), debt=Decimal("0"))), \
             patch("services.white_internet_service.create_purchase_debit", new_callable=AsyncMock) as mock_debit, \
             patch("services.white_internet_service.WhiteInternetService.get_or_create_white_internet_tariff", return_value=tariff), \
             patch("services.white_internet_service.get_or_create_current_version", return_value=tariff_version), \
             patch("database.repositories.white_internet_repo.renew_subscription_atomic") as mock_renew_atomic:

            mock_renew_atomic.return_value = sub

            ok, msg, result = await WhiteInternetService.renew_subscription(mock_session, user_id=42)

            self.assertTrue(ok)
            # Verify debit called with 450.00 RUB for 2 devices
            mock_debit.assert_awaited_once()
            self.assertEqual(mock_debit.await_args[1]["amount"], Decimal("450.00"))
            # Verify atomic repo call with base_bytes = 100 GiB
            mock_renew_atomic.assert_awaited_once_with(
                mock_session,
                subscription_id=1,
                quote_id=mock_renew_atomic.await_args[1]["quote_id"],
                price_rub=Decimal("450.00"),
                duration_days=30,
                base_bytes=100 * 1024**3,
            )

    async def test_renew_subscription_horizon_check_rejected(self):
        """Subscription expiring in 35 days cannot renew (+30 days = 65 days > 60 days max)."""
        sub = WhiteInternetSubscription(
            id=1,
            user_id=42,
            origin_node_id=10,
            status=WhiteInternetStatus.ACTIVE,
            device_limit=1,
            expires_at=self.now + timedelta(days=35),
            base_traffic_bytes=50 * 1024**3,
            extra_traffic_bytes=0,
        )

        mock_session = AsyncMock()
        mock_session.scalar = AsyncMock(return_value=self.server)
        tariff = Tariff(id=1, service_type="white_internet", duration_days=30, price_rub=250)
        tariff_version = TariffVersion(
            id=1,
            tariff_id=1,
            version_number=1,
            name_snapshot="Белый Интернет 50 ГБ",
            service_type="white_internet",
            device_limit=1,
            price_rub=Decimal("250.00"),
            duration_hours=720,
            base_quota_bytes=50 * 1024**3,
        )

        with patch("services.white_internet_service.now_utc", return_value=self.now), \
             patch("services.white_internet_service.lock_checkout_user", return_value=self.user), \
             patch("database.repositories.white_internet_repo.get_subscription_by_user_id", return_value=sub), \
             patch("services.white_internet_service.WhiteInternetService.get_or_create_white_internet_tariff", return_value=tariff), \
             patch("services.white_internet_service.get_or_create_current_version", return_value=tariff_version), \
             patch("services.white_internet_service.create_purchase_debit", new_callable=AsyncMock) as mock_debit:

            ok, msg, result = await WhiteInternetService.renew_subscription(mock_session, user_id=42)

            self.assertFalse(ok)
            self.assertEqual(msg, texts.WL_RENEWAL_HORIZON_EXCEEDED)
            mock_debit.assert_not_called()

    async def test_renew_subscription_quota_cap_150_gib_rollover(self):
        """Rollover extra bytes must be capped so total does not exceed 150 GiB."""
        mock_session = AsyncMock()
        now = self.now

        # Case A: device_limit = 2 (base = 100 GiB), extra_traffic_bytes = 70 GiB
        # Total extra rollover must be min(70 GiB, 150 - 100 = 50 GiB) -> 50 GiB
        sub2 = WhiteInternetSubscription(
            id=1,
            user_id=42,
            origin_node_id=10,
            status=WhiteInternetStatus.ACTIVE,
            device_limit=2,
            expires_at=now + timedelta(days=5),
            base_traffic_bytes=100 * 1024**3,
            extra_traffic_bytes=70 * 1024**3,
            traffic_used_bytes=0,
            desired_version=1,
        )

        with patch("database.repositories.white_internet_repo.get_subscription_with_lock", return_value=sub2):
            renewed = await white_internet_repo.renew_subscription_atomic(
                mock_session,
                subscription_id=1,
                quote_id=10,
                price_rub=Decimal("450.00"),
                duration_days=30,
                base_bytes=100 * 1024**3,
                now=now,
            )
            self.assertEqual(renewed.base_traffic_bytes, 100 * 1024**3)
            self.assertEqual(renewed.extra_traffic_bytes, 50 * 1024**3)
            self.assertEqual(renewed.traffic_limit_bytes, 150 * 1024**3)

        # Case B: device_limit = 3 (base = 150 GiB), extra_traffic_bytes = 50 GiB
        # Total extra rollover must be min(50 GiB, 150 - 150 = 0 GiB) -> 0 GiB
        sub3 = WhiteInternetSubscription(
            id=2,
            user_id=42,
            origin_node_id=10,
            status=WhiteInternetStatus.ACTIVE,
            device_limit=3,
            expires_at=now + timedelta(days=5),
            base_traffic_bytes=150 * 1024**3,
            extra_traffic_bytes=50 * 1024**3,
            traffic_used_bytes=0,
            desired_version=1,
        )

        with patch("database.repositories.white_internet_repo.get_subscription_with_lock", return_value=sub3):
            renewed = await white_internet_repo.renew_subscription_atomic(
                mock_session,
                subscription_id=2,
                quote_id=11,
                price_rub=Decimal("650.00"),
                duration_days=30,
                base_bytes=150 * 1024**3,
                now=now,
            )
            self.assertEqual(renewed.base_traffic_bytes, 150 * 1024**3)
            self.assertEqual(renewed.extra_traffic_bytes, 0)
            self.assertEqual(renewed.traffic_limit_bytes, 150 * 1024**3)


class TestWhiteInternetAdminAndCooldown(unittest.IsolatedAsyncioTestCase):
    """Test topup admin restrictions and device reset cooldown."""

    async def test_topup_quota_admin_only(self):
        mock_session = AsyncMock()
        # Non-admin rejected
        with patch("services.white_internet_service.is_admin", return_value=False):
            ok, msg, grant = await WhiteInternetService.topup_quota(
                mock_session, user_id=42, pack_gb=10, actor_telegram_id=12345
            )
            self.assertFalse(ok)
            self.assertEqual(msg, texts.WL_ADMIN_ONLY_ALERT)

    async def test_reset_devices_cooldown_enforcement(self):
        mock_session = AsyncMock()
        now = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)
        sub = WhiteInternetSubscription(
            id=1,
            user_id=42,
            origin_node_id=10,
            active_hwids={"dev-1": now.isoformat()},
            last_device_reset_at=None,
        )

        mock_session.get = AsyncMock(return_value=sub)

        # 1. First reset succeeds
        res = await white_internet_repo.reset_active_hwids_atomic(mock_session, 1, now=now)
        self.assertTrue(res)
        self.assertEqual(sub.active_hwids, {})
        self.assertEqual(sub.last_device_reset_at, now)

        # 2. Second reset after 5 minutes (cooldown 15 min = 900s) raises WhiteInternetResetCooldownError
        five_min_later = now + timedelta(minutes=5)
        with self.assertRaises(WhiteInternetResetCooldownError) as ctx:
            await white_internet_repo.reset_active_hwids_atomic(
                mock_session, 1, cooldown_seconds=900, now=five_min_later
            )
        self.assertEqual(ctx.exception.remaining_seconds, 600)

        # 3. Third reset after 16 minutes succeeds
        sixteen_min_later = now + timedelta(minutes=16)
        sub.active_hwids = {"dev-2": sixteen_min_later.isoformat()}
        res = await white_internet_repo.reset_active_hwids_atomic(
            mock_session, 1, cooldown_seconds=900, now=sixteen_min_later
        )
        self.assertTrue(res)
        self.assertEqual(sub.active_hwids, {})
        self.assertEqual(sub.last_device_reset_at, sixteen_min_later)


class TestWhiteInternetKeyboardAndDecoupling(unittest.TestCase):
    """Test keyboard UI dynamic buttons and strict protocol decoupling AST invariants."""

    def test_strict_protocol_decoupling_ast(self):
        """Rule 7 AGENTS.md: White Internet modules must never import or reference SubscriptionService."""
        files_to_check = [
            "bot/handlers/white_internet.py",
            "bot/handlers/white_internet_web.py",
            "services/white_internet_service.py",
        ]

        forbidden_names = {"SubscriptionService", "AmneziaClient"}

        for filepath in files_to_check:
            full_path = os.path.join(os.getcwd(), filepath)
            if not os.path.exists(full_path):
                continue
            with open(full_path, "r", encoding="utf-8") as f:
                content = f.read()

            parsed = ast.parse(content, filename=filepath)
            for node in ast.walk(parsed):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        for forbidden in forbidden_names:
                            self.assertNotIn(
                                forbidden,
                                alias.name,
                                f"Forbidden import '{alias.name}' in {filepath}",
                            )
                elif isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    for alias in node.names:
                        for forbidden in forbidden_names:
                            self.assertNotIn(
                                forbidden,
                                alias.name,
                                f"Forbidden import '{alias.name}' from '{module}' in {filepath}",
                            )
                            self.assertNotIn(
                                forbidden,
                                module,
                                f"Forbidden module '{module}' in {filepath}",
                            )
                elif isinstance(node, ast.Name):
                    for forbidden in forbidden_names:
                        self.assertNotEqual(
                            node.id,
                            forbidden,
                            f"Forbidden symbol reference '{forbidden}' in {filepath}",
                        )

    def test_keyboard_renewal_button_and_admin_buttons(self):
        from bot.handlers.white_internet import get_white_internet_overview_keyboard

        now = datetime.now(timezone.utc)

        # 1. Sub expiring in 10 days -> Renewal button MUST be present
        sub_soon = WhiteInternetSubscription(
            id=1,
            status=WhiteInternetStatus.ACTIVE,
            expires_at=now + timedelta(days=10),
            device_limit=2,
            token="token123",
            is_trial=False,
        )
        kb_user = get_white_internet_overview_keyboard(
            sub_soon, bot_domain="test.domain", is_admin_user=False
        )
        buttons_user = [btn.text for row in kb_user.inline_keyboard for btn in row]

        # Must have renewal button for 450 ₽
        self.assertTrue(any("450" in text for text in buttons_user))
        # Paid user HAS topup and add device (self-service up to max 3 devices)
        self.assertTrue(any("Докупить трафик" in text for text in buttons_user))
        self.assertTrue(any("Добавить устройство" in text for text in buttons_user))
        # Refresh button must NEVER be present
        self.assertFalse(any("Обновить расход" in text for text in buttons_user))

        # Paid user at max limit (3 devices) cannot add more devices
        sub_max = WhiteInternetSubscription(
            id=4,
            status=WhiteInternetStatus.ACTIVE,
            expires_at=now + timedelta(days=10),
            device_limit=3,
            token="tokenmax",
            is_trial=False,
        )
        kb_max = get_white_internet_overview_keyboard(
            sub_max, bot_domain="test.domain", is_admin_user=False
        )
        buttons_max = [btn.text for row in kb_max.inline_keyboard for btn in row]
        self.assertTrue(any("Докупить трафик" in text for text in buttons_max))
        self.assertFalse(any("Добавить устройство" in text for text in buttons_max))

        # Trial user cannot topup or add device
        sub_trial = WhiteInternetSubscription(
            id=3,
            status=WhiteInternetStatus.ACTIVE,
            expires_at=now + timedelta(days=2),
            device_limit=1,
            token="tokentrial",
            is_trial=True,
        )
        kb_trial = get_white_internet_overview_keyboard(
            sub_trial, bot_domain="test.domain", is_admin_user=False
        )
        buttons_trial = [btn.text for row in kb_trial.inline_keyboard for btn in row]
        self.assertFalse(any("Докупить трафик" in text for text in buttons_trial))
        self.assertFalse(any("Добавить устройство" in text for text in buttons_trial))

        # 2. Sub expiring in 45 days -> Renewal button MUST NOT be present (> 30 days remaining)
        sub_far = WhiteInternetSubscription(
            id=2,
            status=WhiteInternetStatus.ACTIVE,
            expires_at=now + timedelta(days=45),
            device_limit=1,
            token="token123",
        )
        kb_far = get_white_internet_overview_keyboard(
            sub_far, bot_domain="test.domain", is_admin_user=False
        )
        buttons_far = [btn.text for row in kb_far.inline_keyboard for btn in row]
        self.assertFalse(any("Продлить" in text for text in buttons_far))

        # 3. Admin user on sub with 1 device -> Has topup and add-device buttons
        kb_admin = get_white_internet_overview_keyboard(
            sub_far, bot_domain="test.domain", is_admin_user=True
        )
        buttons_admin = [btn.text for row in kb_admin.inline_keyboard for btn in row]
        self.assertTrue(any("Докупить трафик" in text for text in buttons_admin))
        self.assertTrue(any("Добавить устройство" in text for text in buttons_admin))

        # 4. Admin user on sub with 3 devices -> Has topup, but NOT add-device button
        sub_max = WhiteInternetSubscription(
            id=3,
            status=WhiteInternetStatus.ACTIVE,
            expires_at=now + timedelta(days=5),
            device_limit=3,
            token="token123",
        )
        kb_admin_max = get_white_internet_overview_keyboard(
            sub_max, bot_domain="test.domain", is_admin_user=True
        )
        buttons_admin_max = [btn.text for row in kb_admin_max.inline_keyboard for btn in row]
        self.assertTrue(any("Докупить трафик" in text for text in buttons_admin_max))
        self.assertFalse(any("Добавить устройство" in text for text in buttons_admin_max))
        # Renewal for 3 devices must be 650 ₽
        self.assertTrue(any("650" in text for text in buttons_admin_max))


class TestAuditRemediations(unittest.IsolatedAsyncioTestCase):
    """Test bug fixes identified during audit remediation."""

    async def test_add_device_slot_atomic_none_expires_at(self):
        """add_device_slot_atomic must not raise TypeError when expires_at is None."""
        sub = WhiteInternetSubscription(
            id=10,
            status=WhiteInternetStatus.ACTIVE,
            expires_at=None,
            device_limit=1,
            base_traffic_bytes=50 * 1024**3,
            extra_traffic_bytes=0,
        )
        mock_session = AsyncMock()
        with patch("database.repositories.white_internet_repo.get_subscription_with_lock", return_value=sub):
            updated = await white_internet_repo.add_device_slot_atomic(
                mock_session,
                subscription_id=10,
            )
        self.assertEqual(updated.device_limit, 2)
        self.assertEqual(updated.extra_traffic_bytes, 50 * 1024**3)

    async def test_topup_quota_atomic_none_expires_at(self):
        """topup_quota_atomic must not raise TypeError when expires_at is None."""
        sub = WhiteInternetSubscription(
            id=11,
            status=WhiteInternetStatus.ACTIVE,
            expires_at=None,
            device_limit=1,
            base_traffic_bytes=50 * 1024**3,
            extra_traffic_bytes=0,
        )
        mock_session = AsyncMock()
        with patch("database.repositories.white_internet_repo.get_subscription_with_lock", return_value=sub):
            added_bytes = await white_internet_repo.topup_quota_atomic(
                mock_session,
                subscription_id=11,
                quote_id=999,
                pack_gb=10,
                price_rub=Decimal("40.00"),
            )
        self.assertEqual(sub.extra_traffic_bytes, 10 * 1024**3)
        self.assertEqual(added_bytes, 10 * 1024**3)

    def test_max_device_limit_bounded_to_db_constraint(self):
        """WHITE_INTERNET_MAX_DEVICE_LIMIT must never exceed 3 due to PostgreSQL CheckConstraint."""
        from config.constants import WHITE_INTERNET_MAX_DEVICE_LIMIT
        self.assertLessEqual(WHITE_INTERNET_MAX_DEVICE_LIMIT, 3)
        self.assertGreaterEqual(WHITE_INTERNET_MAX_DEVICE_LIMIT, 1)

    def test_hwid_ttl_hours_uniform(self):
        """WHITE_INTERNET_HWID_TTL_HOURS must be defined and positive."""
        from config.constants import WHITE_INTERNET_HWID_TTL_HOURS
        self.assertIsInstance(WHITE_INTERNET_HWID_TTL_HOURS, int)
        self.assertGreater(WHITE_INTERNET_HWID_TTL_HOURS, 0)


if __name__ == "__main__":
    unittest.main()
