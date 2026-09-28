import unittest

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from services.referral_bonus import calculate_referral_bonus



class TestReferralBonusCalculation(unittest.TestCase):
    def test_referral_bonus_default_rate_is_fifteen_percent(self):
        assert calculate_referral_bonus(1) == Decimal(0)
        assert calculate_referral_bonus(10) == Decimal(1)
        assert calculate_referral_bonus(30) == Decimal(4)
        assert calculate_referral_bonus(99) == Decimal(14)
        assert calculate_referral_bonus(100) == Decimal(15)
        assert calculate_referral_bonus(999) == Decimal(149)
        assert calculate_referral_bonus(1000) == Decimal(150)

    def test_referral_bonus_supports_explicit_tier_rates(self):
        for rate, expected_100, expected_1000 in (
            (Decimal("0.15"), Decimal(15), Decimal(150)),
            (Decimal("0.20"), Decimal(20), Decimal(200)),
            (Decimal("0.25"), Decimal(25), Decimal(250)),
            (Decimal("0.30"), Decimal(30), Decimal(300)),
        ):
            assert calculate_referral_bonus(100, rate=rate) == expected_100
            assert calculate_referral_bonus(1000, rate=rate) == expected_1000

    def test_referral_bonus_rejects_non_positive_amounts(self):
        assert calculate_referral_bonus(0) == Decimal(0)
        assert calculate_referral_bonus(-100) == Decimal(0)


class TestReferralTiers(unittest.TestCase):
    def test_referral_tier_progression(self):
        from services.referral_bonus import get_referral_tier

        # Tier 1: 0..4 -> 15% (Старт)
        for cnt in (0, 1, 2, 3, 4):
            t = get_referral_tier(cnt)
            assert t.rate == Decimal("0.15")
            assert t.name == "Старт"
            assert t.needed_for_next == (5 - cnt)
            assert t.next_tier_name == "Активист"
            assert t.next_rate == Decimal("0.20")

        # Tier 2: 5..9 -> 20% (Активист)
        for cnt in (5, 6, 7, 8, 9):
            t = get_referral_tier(cnt)
            assert t.rate == Decimal("0.20")
            assert t.name == "Активист"
            assert t.needed_for_next == (10 - cnt)
            assert t.next_tier_name == "Мастер"
            assert t.next_rate == Decimal("0.25")

        # Tier 3: 10..14 -> 25% (Мастер)
        for cnt in (10, 11, 12, 13, 14):
            t = get_referral_tier(cnt)
            assert t.rate == Decimal("0.25")
            assert t.name == "Мастер"
            assert t.needed_for_next == (15 - cnt)
            assert t.next_tier_name == "Амбассадор"
            assert t.next_rate == Decimal("0.30")

        # Tier 4: 15+ -> 30% (Амбассадор)
        for cnt in (15, 20, 50, 100):
            t = get_referral_tier(cnt)
            assert t.rate == Decimal("0.30")
            assert t.name == "Амбассадор"
            assert t.needed_for_next is None
            assert t.next_tier_name is None
            assert t.next_rate is None


class TestMaskTelegramId(unittest.TestCase):
    def test_masking_standard_ids(self):
        from bot.texts.user.referral import mask_telegram_id

        assert mask_telegram_id(8141287721) == "814***21"
        assert mask_telegram_id("8141287721") == "814***21"
        assert mask_telegram_id(123456789) == "123***89"
        assert mask_telegram_id(12345) == "123***45"

    def test_masking_short_ids(self):
        from bot.texts.user.referral import mask_telegram_id

        assert mask_telegram_id(1234) == "1***4"
        assert mask_telegram_id(12) == "1***2"
        assert mask_telegram_id(1) == "***"


class TestReferralBonusLedgerEntryShape(unittest.TestCase):
    def test_ledger_entry_created_with_payment_id_none(self):
        import asyncio

        from database.models import AccountLedgerEntry
        from services.referral_bonus import grant_referral_bonus_for_topup

        captured_entry = {}

        referrer = MagicMock()
        referrer.id = 1
        referrer.telegram_id = 111
        referrer.is_banned = False

        purchaser = MagicMock()
        purchaser.id = 4
        purchaser.telegram_id = 222
        purchaser.referred_by = 111

        def fake_add(entry):
            if isinstance(entry, AccountLedgerEntry):
                captured_entry["obj"] = entry

        session = AsyncMock()
        mock_ctx = __import__('unittest.mock', fromlist=['MagicMock']).MagicMock()
        mock_ctx.__aenter__ = __import__('unittest.mock', fromlist=['AsyncMock']).AsyncMock(return_value=session)
        mock_ctx.__aexit__ = __import__('unittest.mock', fromlist=['AsyncMock']).AsyncMock(return_value=None)
        session.begin_nested = __import__('unittest.mock', fromlist=['MagicMock']).MagicMock(return_value=mock_ctx)
        # 1. purchaser, 2. referrer, 3. active_referrals_count (0), 4. existing check (None)
        session.scalar = AsyncMock(side_effect=[purchaser, referrer, 0, None])
        session.add = fake_add
        session.flush = AsyncMock()

        asyncio.run(
            grant_referral_bonus_for_topup(
                session,
                purchaser_user_id=4,
                payment_id=42,
                topup_amount=30,
            )
        )

        entry = captured_entry.get("obj")
        assert entry is not None, "AccountLedgerEntry was never added to session"
        assert entry.payment_id is None
        assert entry.entry_type == "admin_adjustment"
        assert entry.amount == Decimal(4)  # 15% of 30
        assert entry.metadata_["topup_payment_id"] == 42
        assert entry.metadata_["bonus_rate"] == "0.15"
        assert entry.metadata_["tier_name"] == "Старт"

    def test_reverse_referral_bonus_for_topup(self):
        import asyncio

        from database.models import AccountLedgerEntry
        from services.referral_bonus import reverse_referral_bonus_for_topup

        captured_entry = {}

        existing_bonus_credit = MagicMock()
        existing_bonus_credit.id = 100
        existing_bonus_credit.user_id = 1
        existing_bonus_credit.amount = Decimal(10)
        existing_bonus_credit.metadata_ = {"topup_payment_id": 42, "source_type": "referral_bonus"}

        def fake_add(entry):
            if isinstance(entry, AccountLedgerEntry):
                captured_entry["obj"] = entry

        scalars_mock = MagicMock()
        scalars_mock.all.return_value = [existing_bonus_credit]

        session = AsyncMock()
        session.scalar = AsyncMock(return_value=None)
        session.scalars = AsyncMock(return_value=scalars_mock)
        session.add = fake_add
        session.flush = AsyncMock()

        reversed_amount = asyncio.run(
            reverse_referral_bonus_for_topup(session, payment_id=42)
        )

        assert reversed_amount == Decimal(10)
        entry = captured_entry.get("obj")
        assert entry is not None
        assert entry.user_id == 1
        assert entry.amount == Decimal(-10)
        assert entry.reversal_of_id is None
        assert entry.payment_id is None
        assert entry.metadata_["topup_payment_id"] == 42
        assert entry.metadata_["original_credit_id"] == 100

    def test_reverse_referral_bonus_for_order(self):
        import asyncio
        import uuid

        from database.models import AccountLedgerEntry
        from services.referral_bonus import reverse_referral_bonus_for_topup

        captured_entry = {}

        order_uuid = uuid.uuid4()
        existing_bonus_credit = MagicMock()
        existing_bonus_credit.id = 101
        existing_bonus_credit.user_id = 1
        existing_bonus_credit.amount = Decimal(25)
        existing_bonus_credit.metadata_ = {"topup_order_id": str(order_uuid), "source_type": "referral_bonus"}

        def fake_add(entry):
            if isinstance(entry, AccountLedgerEntry):
                captured_entry["obj"] = entry

        scalars_mock = MagicMock()
        scalars_mock.all.return_value = [existing_bonus_credit]

        session = AsyncMock()
        session.scalar = AsyncMock(return_value=None)
        session.scalars = AsyncMock(return_value=scalars_mock)
        session.add = fake_add
        session.flush = AsyncMock()

        reversed_amount = asyncio.run(
            reverse_referral_bonus_for_topup(session, order_id=order_uuid)
        )

        assert reversed_amount == Decimal(25)
        entry = captured_entry.get("obj")
        assert entry is not None
        assert entry.user_id == 1
        assert entry.amount == Decimal(-25)
        assert entry.reversal_of_id is None
        assert entry.payment_id is None
        assert entry.metadata_["topup_order_id"] == str(order_uuid)
        assert entry.metadata_["original_credit_id"] == 101

    def test_reverse_referral_bonus_with_invalid_uuid_raises_value_error(self):
        import asyncio
        from services.referral_bonus import reverse_referral_bonus_for_topup

        session = AsyncMock()
        with self.assertRaises(ValueError) as cm:
            asyncio.run(
                reverse_referral_bonus_for_topup(session, order_id="invalid-not-a-uuid")
            )
        self.assertIn("Invalid order_id for referral reversal", str(cm.exception))

    def test_reverse_referral_bonus_with_integer_order_id_raises_value_error(self):
        import asyncio
        from services.referral_bonus import reverse_referral_bonus_for_topup

        session = AsyncMock()
        with self.assertRaises(ValueError) as cm:
            asyncio.run(
                reverse_referral_bonus_for_topup(session, order_id=12345)
            )
        self.assertIn("Invalid order_id for referral reversal", str(cm.exception))

    def test_reverse_referral_bonus_is_idempotent_when_reversal_already_exists(self):
        import asyncio
        import uuid

        from services.referral_bonus import reverse_referral_bonus_for_topup

        order_uuid = uuid.uuid4()
        existing_bonus_credit = MagicMock()
        existing_bonus_credit.id = 101
        existing_bonus_credit.user_id = 1
        existing_bonus_credit.amount = Decimal(25)
        existing_bonus_credit.metadata_ = {
            "topup_order_id": str(order_uuid),
            "source_type": "referral_bonus",
        }

        existing_reversal = MagicMock()
        existing_reversal.entry_type = "admin_adjustment"
        existing_reversal.amount = Decimal(-25)

        captured_entries = []
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = [existing_bonus_credit]

        session = AsyncMock()
        session.scalars = AsyncMock(return_value=scalars_mock)
        session.scalar = AsyncMock(return_value=existing_reversal)
        session.add = lambda entry: captured_entries.append(entry)
        session.flush = AsyncMock()

        reversed_amount = asyncio.run(
            reverse_referral_bonus_for_topup(session, order_id=order_uuid)
        )

        self.assertEqual(reversed_amount, Decimal(25))
        self.assertEqual(len(captured_entries), 0)

    def test_reverse_referral_bonus_returns_zero_when_no_credits_exist(self):
        import asyncio
        import uuid

        from services.referral_bonus import reverse_referral_bonus_for_topup

        order_uuid = uuid.uuid4()
        captured_entries = []
        scalars_mock = MagicMock()
        scalars_mock.all.return_value = []

        session = AsyncMock()
        session.scalars = AsyncMock(return_value=scalars_mock)
        session.scalar = AsyncMock(return_value=None)
        session.add = lambda entry: captured_entries.append(entry)
        session.flush = AsyncMock()

        reversed_amount = asyncio.run(
            reverse_referral_bonus_for_topup(session, order_id=order_uuid)
        )

        self.assertEqual(reversed_amount, Decimal(0))
        self.assertEqual(len(captured_entries), 0)

    def test_reverse_referral_bonus_returns_zero_when_both_ids_none(self):
        import asyncio
        from services.referral_bonus import reverse_referral_bonus_for_topup

        session = AsyncMock()
        reversed_amount = asyncio.run(
            reverse_referral_bonus_for_topup(session, payment_id=None, order_id=None)
        )
        self.assertEqual(reversed_amount, Decimal(0))

    def test_reverse_referral_bonus_reverses_purchaser_welcome_bonus_for_matching_order(self):
        import asyncio
        import uuid

        from database.models import AccountLedgerEntry
        from services.referral_bonus import reverse_referral_bonus_for_topup

        captured_entries = []

        order_uuid = uuid.uuid4()

        referrer_credit = MagicMock()
        referrer_credit.id = 101
        referrer_credit.user_id = 1
        referrer_credit.amount = Decimal(25)
        referrer_credit.metadata_ = {"topup_order_id": str(order_uuid), "source_type": "referral_bonus"}

        welcome_credit = MagicMock()
        welcome_credit.id = 102
        welcome_credit.user_id = 4
        welcome_credit.amount = Decimal(25)
        welcome_credit.metadata_ = {
            "topup_order_id": str(order_uuid),
            "reason": "first_topup_welcome",
            "source_type": "referral_bonus",
        }

        def fake_add(entry):
            if isinstance(entry, AccountLedgerEntry):
                captured_entries.append(entry)

        scalars_mock = MagicMock()
        scalars_mock.all.return_value = [referrer_credit, welcome_credit]

        session = AsyncMock()
        session.scalar = AsyncMock(return_value=None)
        session.scalars = AsyncMock(return_value=scalars_mock)
        session.add = fake_add
        session.flush = AsyncMock()

        reversed_amount = asyncio.run(
            reverse_referral_bonus_for_topup(session, order_id=order_uuid)
        )

        assert reversed_amount == Decimal(50)
        assert len(captured_entries) == 2
        p_rev = [e for e in captured_entries if e.user_id == 4][0]
        assert p_rev.amount == Decimal(-25)
        assert p_rev.metadata_["topup_order_id"] == str(order_uuid)
        assert p_rev.metadata_["original_credit_id"] == 102

    def test_reverse_referral_bonus_does_not_reverse_unrelated_order_welcome_bonus(self):
        import asyncio
        import uuid

        from services.referral_bonus import reverse_referral_bonus_for_topup

        captured_entries = []

        order2_uuid = uuid.uuid4()

        referrer_credit = MagicMock()
        referrer_credit.id = 201
        referrer_credit.user_id = 1
        referrer_credit.amount = Decimal(50)
        referrer_credit.metadata_ = {"topup_order_id": str(order2_uuid), "source_type": "referral_bonus"}

        scalars_mock = MagicMock()
        scalars_mock.all.return_value = [referrer_credit]

        session = AsyncMock()
        session.scalar = AsyncMock(return_value=None)
        session.scalars = AsyncMock(return_value=scalars_mock)
        session.add = lambda entry: captured_entries.append(entry)
        session.flush = AsyncMock()

        reversed_amount = asyncio.run(
            reverse_referral_bonus_for_topup(session, order_id=order2_uuid)
        )

        assert reversed_amount == Decimal(50)
        assert all(e.user_id != 4 for e in captured_entries)




class TestGrantReferralBonusForTopup(unittest.TestCase):
    def test_first_topup_bonus_credits_referrer_and_not_balance_welcome_bonus(self):
        import asyncio

        from database.models import AccountLedgerEntry
        from services.referral_bonus import grant_referral_bonus_for_topup

        added_entries = []

        referrer = MagicMock()
        referrer.id = 10
        referrer.telegram_id = 1000
        referrer.is_banned = False

        purchaser = MagicMock()
        purchaser.id = 20
        purchaser.telegram_id = 2000
        purchaser.referred_by = 1000

        def fake_add(entry):
            if isinstance(entry, AccountLedgerEntry):
                added_entries.append(entry)

        session = AsyncMock()
        mock_ctx = __import__('unittest.mock', fromlist=['MagicMock']).MagicMock()
        mock_ctx.__aenter__ = __import__('unittest.mock', fromlist=['AsyncMock']).AsyncMock(return_value=session)
        mock_ctx.__aexit__ = __import__('unittest.mock', fromlist=['AsyncMock']).AsyncMock(return_value=None)
        session.begin_nested = __import__('unittest.mock', fromlist=['MagicMock']).MagicMock(return_value=mock_ctx)
        # 1. purchaser, 2. referrer, 3. active_referrals_count (0 -> 15% rate), 4. existing check (None)
        session.scalar = AsyncMock(side_effect=[purchaser, referrer, 0, None])
        session.add = fake_add
        session.flush = AsyncMock()

        res = asyncio.run(
            grant_referral_bonus_for_topup(
                session,
                purchaser_user_id=20,
                payment_id=101,
                topup_amount=500,
            )
        )

        assert len(added_entries) == 1
        referrer_entry = added_entries[0]

        assert referrer_entry.user_id == 10
        assert referrer_entry.amount == Decimal(75)  # 15% of 500
        assert referrer_entry.metadata_["bonus_rate"] == "0.15"
        assert referrer_entry.metadata_["tier_name"] == "Старт"

        # Purchaser gets discount at checkout rather than bonus credit on balance
        assert res.purchaser_welcome_bonus == Decimal(0)
        assert res.referrer_bonus == Decimal(75)


    def test_second_topup_credits_only_referrer(self):
        import asyncio

        from database.models import AccountLedgerEntry
        from services.referral_bonus import grant_referral_bonus_for_topup

        added_entries = []

        referrer = MagicMock()
        referrer.id = 10
        referrer.telegram_id = 1000
        referrer.is_banned = False

        purchaser = MagicMock()
        purchaser.id = 20
        purchaser.telegram_id = 2000
        purchaser.referred_by = 1000

        def fake_add(entry):
            if isinstance(entry, AccountLedgerEntry):
                added_entries.append(entry)

        session = AsyncMock()
        mock_ctx = __import__('unittest.mock', fromlist=['MagicMock']).MagicMock()
        mock_ctx.__aenter__ = __import__('unittest.mock', fromlist=['AsyncMock']).AsyncMock(return_value=session)
        mock_ctx.__aexit__ = __import__('unittest.mock', fromlist=['AsyncMock']).AsyncMock(return_value=None)
        session.begin_nested = __import__('unittest.mock', fromlist=['MagicMock']).MagicMock(return_value=mock_ctx)
        # 1. purchaser, 2. referrer, 3. active_referrals_count (5 -> 20% rate "Активист"), 4. existing check (None)
        session.scalar = AsyncMock(side_effect=[purchaser, referrer, 5, None])
        session.add = fake_add
        session.flush = AsyncMock()

        asyncio.run(
            grant_referral_bonus_for_topup(
                session,
                purchaser_user_id=20,
                payment_id=102,
                topup_amount=1000,
            )
        )

        assert len(added_entries) == 1
        referrer_entry = added_entries[0]
        assert referrer_entry.user_id == 10
        assert referrer_entry.amount == Decimal(200)  # 20% of 1000
        assert referrer_entry.metadata_["bonus_rate"] == "0.20"
        assert referrer_entry.metadata_["tier_name"] == "Активист"


    def test_reverse_referral_bonus_reverses_both_referrer_and_purchaser_bonus(self):
        import asyncio

        from database.models import AccountLedgerEntry
        from services.referral_bonus import reverse_referral_bonus_for_topup

        added_entries = []

        referrer_credit = MagicMock()
        referrer_credit.id = 100
        referrer_credit.user_id = 10
        referrer_credit.amount = Decimal(50)
        referrer_credit.metadata_ = {
            "topup_payment_id": 101,
            "source_type": "referral_bonus",
        }

        purchaser_credit = MagicMock()
        purchaser_credit.id = 101
        purchaser_credit.user_id = 20
        purchaser_credit.amount = Decimal(50)
        purchaser_credit.metadata_ = {
            "topup_payment_id": 101,
            "reason": "first_topup_welcome",
            "source_type": "referral_bonus",
        }

        def fake_add(entry):
            if isinstance(entry, AccountLedgerEntry):
                added_entries.append(entry)

        scalars_mock = MagicMock()
        scalars_mock.all.return_value = [referrer_credit, purchaser_credit]

        session = AsyncMock()
        session.scalar = AsyncMock(return_value=None)
        session.scalars = AsyncMock(return_value=scalars_mock)
        session.add = fake_add
        session.flush = AsyncMock()

        reversed_amount = asyncio.run(
            reverse_referral_bonus_for_topup(session, payment_id=101)
        )

        assert reversed_amount == Decimal(100)
        assert len(added_entries) == 2
        assert added_entries[0].user_id == 10
        assert added_entries[0].amount == Decimal(-50)
        assert added_entries[1].user_id == 20
        assert added_entries[1].amount == Decimal(-50)

    def test_reverse_referral_bonus_does_not_create_allocations(self):
        import asyncio

        from database.models import AccountLedgerAllocation, AccountLedgerEntry
        from services.referral_bonus import reverse_referral_bonus_for_topup

        added_entries = []
        added_allocations = []

        referrer_credit = MagicMock()
        referrer_credit.id = 100
        referrer_credit.user_id = 10
        referrer_credit.amount = Decimal(50)
        referrer_credit.metadata_ = {
            "topup_payment_id": 101,
            "source_type": "referral_bonus",
        }

        def fake_add(entry):
            if isinstance(entry, AccountLedgerEntry):
                added_entries.append(entry)
            if isinstance(entry, AccountLedgerAllocation):
                added_allocations.append(entry)

        scalars_mock = MagicMock()
        scalars_mock.all.return_value = [referrer_credit]

        session = AsyncMock()
        session.scalar = AsyncMock(return_value=None)
        session.scalars = AsyncMock(return_value=scalars_mock)
        session.add = fake_add
        session.flush = AsyncMock()

        reversed_amount = asyncio.run(
            reverse_referral_bonus_for_topup(session, payment_id=101)
        )

        assert reversed_amount == Decimal(50)
        assert len(added_entries) == 1
        assert added_entries[0].amount == Decimal(-50)
        assert added_allocations == []



    def test_grant_referral_bonus_for_topup_tiered_rates(self):
        import asyncio

        from unittest.mock import AsyncMock, MagicMock

        from sqlalchemy.ext.asyncio import AsyncSession

        from database.models import User
        from services.referral_bonus import grant_referral_bonus_for_topup

        session = AsyncMock(spec=AsyncSession)

        mock_purchaser = MagicMock(spec=User)
        mock_purchaser.id = 10
        mock_purchaser.telegram_id = 200
        mock_purchaser.referred_by = 100

        mock_referrer = MagicMock(spec=User)
        mock_referrer.id = 20
        mock_referrer.telegram_id = 100
        mock_referrer.is_banned = False

        # 1. purchaser, 2. referrer, 3. active_count=10 (tier 3: 25% "Мастер"), 4. existing check (None)
        session.scalar.side_effect = [mock_purchaser, mock_referrer, 10, None]

        res = asyncio.run(grant_referral_bonus_for_topup(session, purchaser_user_id=10, payment_id=1, topup_amount=Decimal(100)))

        # Tier 3 (Мастер) -> 25% of 100 = 25
        assert res.referrer_bonus == Decimal(25)
        assert res.purchaser_welcome_bonus == Decimal(0)

    def test_get_referral_bonus_balance_clamped_to_bonus_available(self):
        import asyncio
        from sqlalchemy.ext.asyncio import AsyncSession
        from services.referral_bonus import get_referral_bonus_balance
        from database.repositories.account_ledger_repo import AccountBalanceSnapshot

        session = AsyncMock(spec=AsyncSession)
        session.scalar = AsyncMock(return_value=Decimal("100"))

        # balance has bonus_available = 40 (less than 100)
        balance_snap = AccountBalanceSnapshot(
            accounting_position=Decimal(40),
            available=Decimal(40),
            reserved=Decimal(0),
            debt=Decimal(0),
            real_available=Decimal(0),
            bonus_available=Decimal(40),
        )

        with patch("services.referral_bonus.get_account_balance", return_value=balance_snap):
            bonus = asyncio.run(get_referral_bonus_balance(session, user_id=10))
            # Should be clamped to 40
            assert bonus == Decimal(40)

    def test_reverse_referral_bonus_proportional_partial_refunds(self):
        """Verify partial refunds reverse proportional bonus and track idempotency per refund_id."""
        import asyncio
        import uuid
        from services.referral_bonus import reverse_referral_bonus_for_topup

        order_uuid = uuid.uuid4()
        credit = MagicMock()
        credit.id = 501
        credit.user_id = 10
        credit.amount = Decimal("200.00")
        credit.metadata_ = {
            "topup_order_id": str(order_uuid),
            "source_type": "referral_bonus",
            "bonus_rate": "0.20",
        }

        # 1. First partial refund: 100 RUB of 1000 RUB top-up (10%)
        # Expected bonus reversal: 200 * 0.10 = 20 RUB
        session1 = AsyncMock()
        credits_mock1 = MagicMock()
        credits_mock1.all.return_value = [credit]
        prev_rev_mock1 = MagicMock()
        prev_rev_mock1.all.return_value = []

        session1.scalars.side_effect = [credits_mock1, prev_rev_mock1]
        session1.scalar.return_value = None  # no existing reversal for refund_1
        added_entries1 = []
        session1.add = lambda entry: added_entries1.append(entry)

        rev1 = asyncio.run(
            reverse_referral_bonus_for_topup(
                session1,
                order_id=order_uuid,
                refund_amount=Decimal("100.00"),
                original_topup_amount=Decimal("1000.00"),
                refund_id="ref_1",
            )
        )
        self.assertEqual(rev1, Decimal("20.00"))
        self.assertEqual(len(added_entries1), 1)
        self.assertEqual(added_entries1[0].amount, Decimal("-20.00"))
        self.assertIn("ref_1", added_entries1[0].idempotency_key)

        # 2. Second partial refund: 200 RUB of 1000 RUB top-up (20%)
        # First reversal is now in DB (-20 RUB)
        session2 = AsyncMock()
        credits_mock2 = MagicMock()
        credits_mock2.all.return_value = [credit]
        prev_rev_mock2 = MagicMock()
        prev_rev_entry = MagicMock(amount=Decimal("-20.00"))
        prev_rev_mock2.all.return_value = [prev_rev_entry]

        session2.scalars.side_effect = [credits_mock2, prev_rev_mock2]
        session2.scalar.return_value = None  # no existing reversal for ref_2
        added_entries2 = []
        session2.add = lambda entry: added_entries2.append(entry)

        rev2 = asyncio.run(
            reverse_referral_bonus_for_topup(
                session2,
                order_id=order_uuid,
                refund_amount=Decimal("200.00"),
                original_topup_amount=Decimal("1000.00"),
                refund_id="ref_2",
            )
        )
        self.assertEqual(rev2, Decimal("40.00"))
        self.assertEqual(len(added_entries2), 1)
        self.assertEqual(added_entries2[0].amount, Decimal("-40.00"))
        self.assertIn("ref_2", added_entries2[0].idempotency_key)

        # 3. Duplicate delivery of ref_1 -> must return existing and not add new entry
        session3 = AsyncMock()
        credits_mock3 = MagicMock()
        credits_mock3.all.return_value = [credit]
        session3.scalars.side_effect = [credits_mock3]
        existing_ref1 = MagicMock(entry_type="admin_adjustment", amount=Decimal("-20.00"))
        session3.scalar.return_value = existing_ref1
        added_entries3 = []
        session3.add = lambda entry: added_entries3.append(entry)

        rev3 = asyncio.run(
            reverse_referral_bonus_for_topup(
                session3,
                order_id=order_uuid,
                refund_amount=Decimal("100.00"),
                original_topup_amount=Decimal("1000.00"),
                refund_id="ref_1",
            )
        )
        self.assertEqual(rev3, Decimal("20.00"))
        self.assertEqual(len(added_entries3), 0)

    def test_grant_referral_bonus_purchaser_locked_with_for_update(self):
        """Verify purchaser query in grant_referral_bonus_for_topup uses row-level locking."""
        import asyncio
        import uuid
        from database.models import User
        from services.referral_bonus import grant_referral_bonus_for_topup

        session = AsyncMock()
        session.add = MagicMock()
        mock_purchaser = MagicMock(spec=User, id=10, telegram_id=200, referred_by=100)
        mock_referrer = MagicMock(spec=User, id=20, is_banned=False)
        session.scalar.side_effect = [mock_purchaser, mock_referrer, None, 0, None]

        # Intercept scalar call to verify query structure
        captured_queries = []
        async def mock_scalar(query):
            captured_queries.append(query)
            if len(captured_queries) == 1:
                return mock_purchaser
            if len(captured_queries) == 2:
                return mock_referrer
            return None

        session.scalar = mock_scalar
        asyncio.run(
            grant_referral_bonus_for_topup(
                session,
                purchaser_user_id=10,
                order_id=str(uuid.uuid4()),
                topup_amount=Decimal("100.00"),
            )
        )
        # First query is purchaser lookup: must have _for_update_arg set
        self.assertTrue(len(captured_queries) >= 1)
        purchaser_query = captured_queries[0]
        self.assertIsNotNone(purchaser_query._for_update_arg)

    def test_reverse_referral_bonus_series_of_small_refunds_cumulative_no_abuse(self):
        """Verify that 100 x 1 RUB refunds on 1000 RUB top-up reverse exactly 20 RUB referral bonus cumulatively."""
        import asyncio
        import uuid
        from services.referral_bonus import reverse_referral_bonus_for_topup

        order_uuid = uuid.uuid4()
        credit = MagicMock()
        credit.id = 777
        credit.user_id = 10
        credit.amount = Decimal("200.00")
        credit.metadata_ = {
            "topup_order_id": str(order_uuid),
            "source_type": "referral_bonus",
        }

        session = AsyncMock()
        reversal_entries = []

        async def mock_scalars(query):
            m = MagicMock()
            q_str = str(query)
            if "amount > 0" in q_str or "amount > :amount_1" in q_str:
                m.all.return_value = [credit]
            else:
                m.all.return_value = list(reversal_entries)
            return m

        session.scalars = mock_scalars
        session.scalar = AsyncMock(return_value=None)
        session.add = lambda entry: reversal_entries.append(entry)
        session.flush = AsyncMock()

        cumulative_refunded = Decimal("0.00")
        for i in range(1, 101):
            cumulative_refunded += Decimal("1.00")
            asyncio.run(
                reverse_referral_bonus_for_topup(
                    session,
                    order_id=order_uuid,
                    refund_amount=Decimal("1.00"),
                    original_topup_amount=Decimal("1000.00"),
                    total_refunded_amount=cumulative_refunded,
                    refund_id=f"small_ref_{i}",
                )
            )

        total_reversed = sum((abs(e.amount) for e in reversal_entries), Decimal("0"))
        self.assertEqual(total_reversed, Decimal("20.00"))

    def test_get_referral_bonus_balance_with_partial_refund(self):
        """Verify get_referral_bonus_balance subtracts partial reversals rather than zeroing the whole credit."""
        import asyncio
        from database.repositories.account_ledger_repo import AccountBalanceSnapshot
        from services.referral_bonus import get_referral_bonus_balance

        session = AsyncMock()
        session.scalar = AsyncMock(return_value=Decimal("24.00"))

        balance_snap = AccountBalanceSnapshot(
            accounting_position=Decimal("100.00"),
            available=Decimal("100.00"),
            reserved=Decimal("0.00"),
            debt=Decimal("0.00"),
            bonus_available=Decimal("24.00"),
        )
        with patch("services.referral_bonus.get_account_balance", return_value=balance_snap):
            bonus = asyncio.run(get_referral_bonus_balance(session, user_id=10))
            # 40 - 16 = 24 RUB
            self.assertEqual(bonus, Decimal("24.00"))


class TestReferralFirstOrderDiscount(unittest.TestCase):
    def test_create_order_applies_25_pct_discount_for_referred_user(self):
        import asyncio
        from integrations.payment_gateways.base import PaymentInvoice
        from services.order_service import OrderService

        user = MagicMock()
        user.id = 10
        user.telegram_id = 1000
        user.referred_by = 999
        user.current_tariff_id = None
        user.subscription_end = None

        tariff = MagicMock()
        tariff.id = 1
        tariff.name = "Standard"
        tariff.price_rub = Decimal("100.00")
        tariff.duration_days = 30
        tariff.device_limit = 2

        session = AsyncMock()

        async def fake_get(model, pk, **kwargs):
            from database.models import Tariff, User
            if model is User:
                return user
            if model is Tariff:
                return tariff
            return None

        session.get = fake_get
        session.scalar = AsyncMock(return_value=None)  # no existing pending order
        session.add = MagicMock()
        session.flush = AsyncMock()

        mock_gw = AsyncMock()
        mock_gw.create_payment_url.return_value = PaymentInvoice(
            external_id="ext_1",
            payment_url="https://pay.yookassa.ru/test",
        )

        with patch("services.order_service.get_payment_gateway", return_value=mock_gw), \
             patch("database.repositories.users_repo.is_eligible_for_referral_first_discount", return_value=True):
            order = asyncio.run(
                OrderService.create_order(
                    session,
                    user_id=10,
                    tariff_id=1,
                    payment_method="yookassa",
                )
            )

        self.assertEqual(order.amount_rub, Decimal("75.00"))
        self.assertTrue(order.metadata_.get("is_referral_discount"))
        self.assertEqual(order.metadata_.get("discount_rub"), 25)
        self.assertEqual(order.metadata_.get("original_price_rub"), 100)

    def test_create_order_no_discount_for_ineligible_user(self):
        import asyncio
        from integrations.payment_gateways.base import PaymentInvoice
        from services.order_service import OrderService

        user = MagicMock(id=11, telegram_id=1001, referred_by=None, current_tariff_id=None, subscription_end=None)
        tariff = MagicMock(id=1, name="Standard", price_rub=Decimal("100.00"), duration_days=30, device_limit=2)

        async def fake_get(model, pk, **kwargs):
            from database.models import Tariff, User
            if model is User:
                return user
            if model is Tariff:
                return tariff
            return None

        session = AsyncMock()
        session.get = fake_get
        session.scalar = AsyncMock(return_value=None)
        session.add = MagicMock()
        session.flush = AsyncMock()

        mock_gw = AsyncMock()
        mock_gw.create_payment_url.return_value = PaymentInvoice(
            external_id="ext_2",
            payment_url="https://pay.yookassa.ru/test2",
        )

        with patch("services.order_service.get_payment_gateway", return_value=mock_gw), \
             patch("database.repositories.users_repo.is_eligible_for_referral_first_discount", return_value=False):
            order = asyncio.run(
                OrderService.create_order(
                    session,
                    user_id=11,
                    tariff_id=1,
                    payment_method="yookassa",
                )
            )

        self.assertEqual(order.amount_rub, Decimal("100.00"))
        self.assertNotIn("is_referral_discount", order.metadata_)

    def test_pay_from_wallet_applies_25_pct_discount_for_referred_user(self):
        import asyncio
        from database.repositories.account_ledger_repo import AccountBalanceSnapshot
        from services.order_service import OrderService

        user = MagicMock(id=12, telegram_id=1002, referred_by=888, financial_hold=False, current_tariff_id=None, subscription_end=None)
        tariff = MagicMock(id=1, name="Standard", price_rub=Decimal("200.00"), duration_days=30, device_limit=2)

        async def fake_get(model, pk, **kwargs):
            from database.models import Tariff, User
            if model is User:
                return user
            if model is Tariff:
                return tariff
            return None

        session = AsyncMock()
        session.get = fake_get
        session.add = MagicMock()
        session.flush = AsyncMock()

        balance_snap = AccountBalanceSnapshot(
            accounting_position=Decimal("300.00"),
            available=Decimal("300.00"),
            reserved=Decimal("0.00"),
            debt=Decimal("0.00"),
            real_available=Decimal("300.00"),
            bonus_available=Decimal("0.00"),
        )

        with patch("services.order_service.get_account_balance", return_value=balance_snap), \
             patch("services.order_service.create_order_debit") as mock_debit, \
             patch("services.fulfillment_service.FulfillmentService.fulfill_order", new_callable=AsyncMock), \
             patch("database.repositories.users_repo.is_eligible_for_referral_first_discount", return_value=True):
            order = asyncio.run(
                OrderService.pay_from_wallet(
                    session,
                    user_id=12,
                    tariff_id=1,
                )
            )

        # 200 - 25% = 150
        self.assertEqual(order.amount_rub, Decimal("150.00"))
        self.assertTrue(order.metadata_.get("is_referral_discount"))
        self.assertEqual(order.metadata_.get("discount_rub"), 50)
        self.assertEqual(order.metadata_.get("original_price_rub"), 200)
        mock_debit.assert_awaited_once_with(
            session,
            user_id=12,
            amount_rub=Decimal("150.00"),
            order_id=order.id,
            metadata={"description": order.description},
        )


class TestReferralEligibilityAndRanks(unittest.TestCase):
    def test_is_eligible_for_referral_first_discount_no_inviter(self):
        import asyncio
        from database.repositories.users_repo import is_eligible_for_referral_first_discount

        session = AsyncMock()
        user_no_inviter = MagicMock(id=1, telegram_id=100, referred_by=None)
        session.get = AsyncMock(return_value=user_no_inviter)

        self.assertFalse(asyncio.run(is_eligible_for_referral_first_discount(session, 1)))

    def test_is_eligible_for_referral_first_discount_self_referral(self):
        import asyncio
        from database.repositories.users_repo import is_eligible_for_referral_first_discount

        session = AsyncMock()
        user_self = MagicMock(id=1, telegram_id=100, referred_by=100)
        session.get = AsyncMock(return_value=user_self)

        self.assertFalse(asyncio.run(is_eligible_for_referral_first_discount(session, 1)))

    def test_is_eligible_for_referral_first_discount_new_user_eligible(self):
        import asyncio
        from database.repositories.users_repo import is_eligible_for_referral_first_discount

        session = AsyncMock()
        user_invited = MagicMock(id=2, telegram_id=200, referred_by=100)
        session.get = AsyncMock(return_value=user_invited)
        # 1. paid_orders count -> 0, 2. paid_payments count -> 0
        session.scalar = AsyncMock(side_effect=[0, 0])

        self.assertTrue(asyncio.run(is_eligible_for_referral_first_discount(session, 2)))

    def test_is_eligible_for_referral_first_discount_has_paid_order(self):
        import asyncio
        from database.repositories.users_repo import is_eligible_for_referral_first_discount

        session = AsyncMock()
        user_invited = MagicMock(id=3, telegram_id=300, referred_by=100)
        session.get = AsyncMock(return_value=user_invited)
        # paid_orders count -> 1
        session.scalar = AsyncMock(return_value=1)

        self.assertFalse(asyncio.run(is_eligible_for_referral_first_discount(session, 3)))

    def test_get_user_referral_rank_unranked_for_zero_active(self):
        import asyncio
        from database.repositories.users_repo import get_user_referral_rank

        session = AsyncMock()
        with patch("database.repositories.users_repo.get_user_active_referrals_count", return_value=0):
            rank, count = asyncio.run(get_user_referral_rank(session, telegram_id=555))
            self.assertIsNone(rank)
            self.assertEqual(count, 0)

    def test_get_user_referral_rank_for_ranked_user(self):
        import asyncio
        from database.repositories.users_repo import get_user_referral_rank

        session = AsyncMock()
        # active_count = 5, higher_count = 2 (so rank is 3)
        with patch("database.repositories.users_repo.get_user_active_referrals_count", return_value=5):
            session.scalar = AsyncMock(return_value=2)
            rank, count = asyncio.run(get_user_referral_rank(session, telegram_id=777))
            self.assertEqual(rank, 3)
            self.assertEqual(count, 5)
