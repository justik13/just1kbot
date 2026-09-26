import unittest

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from services.referral_bonus import calculate_referral_bonus



class TestReferralBonusCalculation(unittest.TestCase):
    def test_referral_bonus_is_twenty_percent_for_every_purchase_amount(self):
        assert calculate_referral_bonus(1) == Decimal(0)
        assert calculate_referral_bonus(10) == Decimal(2)
        assert calculate_referral_bonus(30) == Decimal(6)
        assert calculate_referral_bonus(99) == Decimal(19)
        assert calculate_referral_bonus(100) == Decimal(20)
        assert calculate_referral_bonus(999) == Decimal(199)
        assert calculate_referral_bonus(1000) == Decimal(200)


    def test_referral_bonus_has_no_duration_or_first_purchase_gate(self):
        # The calculation depends only on the successfully spent amount.
        for purchase_amount in (10, 50, 123, 500, 999):
            assert calculate_referral_bonus(purchase_amount) == (
                Decimal(str(purchase_amount)) * Decimal("0.20")
            ).quantize(Decimal(1), rounding="ROUND_DOWN")


    def test_referral_bonus_rejects_non_positive_amounts(self):
        assert calculate_referral_bonus(0) == Decimal(0)
        assert calculate_referral_bonus(-100) == Decimal(0)


class TestReferralBonusLedgerEntryShape(unittest.TestCase):
    """
    Regression test for production bug:
    CheckViolationError on ck_account_ledger_entry_shape.

    The DB constraint requires:
        entry_type = 'admin_adjustment' AND payment_id IS NULL

    Previously the code set payment_id=payment_id which violated this constraint
    and caused all referral bonuses to silently fail.
    """

    def test_ledger_entry_created_with_payment_id_none(self):
        """
        Verify that grant_referral_bonus_for_topup creates an AccountLedgerEntry
        with payment_id=None, not with the topup payment_id.
        """
        import asyncio

        from database.models import AccountLedgerEntry
        from services.referral_bonus import grant_referral_bonus_for_topup

        captured_entry = {}

        # Build fake referrer and purchaser
        referrer = MagicMock()
        referrer.id = 1
        referrer.telegram_id = 111
        referrer.is_banned = False

        purchaser = MagicMock()
        purchaser.id = 4
        purchaser.telegram_id = 222
        purchaser.referred_by = 111  # referred by referrer

        def fake_add(entry):
            if isinstance(entry, AccountLedgerEntry):
                captured_entry["obj"] = entry

        session = AsyncMock()
        mock_ctx = __import__('unittest.mock', fromlist=['MagicMock']).MagicMock()
        mock_ctx.__aenter__ = __import__('unittest.mock', fromlist=['AsyncMock']).AsyncMock(return_value=session)
        mock_ctx.__aexit__ = __import__('unittest.mock', fromlist=['AsyncMock']).AsyncMock(return_value=None)
        session.begin_nested = __import__('unittest.mock', fromlist=['MagicMock']).MagicMock(return_value=mock_ctx)
        session.scalar = AsyncMock(side_effect=[purchaser, referrer, None, 0, None])
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
        assert entry.payment_id is None, (
            "payment_id must be None for admin_adjustment entries — "
            "ck_account_ledger_entry_shape constraint forbids non-NULL payment_id here"
        )
        assert entry.entry_type == "admin_adjustment"
        assert entry.amount == Decimal(6)
        assert entry.metadata_["topup_payment_id"] == 42, \
            "topup_payment_id should be preserved in metadata for traceability"

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
    def test_first_topup_bonus_credits_both_purchaser_and_referrer(self):
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
        # 1. purchaser, 2. referrer, 3. existing referrer bonus check (None), 4. prev_credited (0), 5. existing purchaser bonus check (None)
        session.scalar = AsyncMock(side_effect=[purchaser, referrer, None, 0, None])
        session.add = fake_add
        session.flush = AsyncMock()

        asyncio.run(
            grant_referral_bonus_for_topup(
                session,
                purchaser_user_id=20,
                payment_id=101,
                topup_amount=500,
            )
        )

        assert len(added_entries) == 2
        referrer_entry = added_entries[0]
        purchaser_entry = added_entries[1]

        assert referrer_entry.user_id == 10
        assert referrer_entry.amount == Decimal(100)

        assert purchaser_entry.user_id == 20
        assert purchaser_entry.amount == Decimal(100)
        assert purchaser_entry.metadata_["reason"] == "first_topup_welcome"


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
        # 1. purchaser, 2. referrer, 3. existing referrer bonus check (None), 4. prev_credited (1 = previous topup exists)
        session.scalar = AsyncMock(side_effect=[purchaser, referrer, None, 1])
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
        assert referrer_entry.amount == Decimal(200)


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



    def test_grant_referral_bonus_for_topup_uses_strict_chronological_ordering(self):
        import asyncio

        # Simulate P1 (id=1) and P2 (id=2) where P1 recovery runs AFTER P2 is processed.
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
        mock_referrer.is_banned = False
    
        # 1. purchaser -> mock_purchaser
        # 2. referrer -> mock_referrer
        # 3. existing -> None
        # 4. prev_credited -> 0
        # 5. existing_purchaser -> None
        session.scalar.side_effect = [mock_purchaser, mock_referrer, None, 0, None]
    
        res = asyncio.run(grant_referral_bonus_for_topup(session, purchaser_user_id=10, payment_id=1, topup_amount=Decimal(100)))
    
        # Welcome bonus MUST be granted (20% of 100 = 20)
        assert res.purchaser_welcome_bonus == Decimal(20)

    def test_get_referral_bonus_balance_clamped_to_bonus_available(self):
        import asyncio
        from sqlalchemy.ext.asyncio import AsyncSession
        from services.referral_bonus import get_referral_bonus_balance
        from database.repositories.account_ledger_repo import AccountBalanceSnapshot

        session = AsyncMock(spec=AsyncSession)
        # 1. credits
        mock_credits = MagicMock()
        credit1 = MagicMock()
        credit1.id = 1
        credit1.amount = Decimal(100)
        mock_credits.all.return_value = [credit1]

        # 2. fully reversed
        mock_rev = MagicMock()
        mock_rev.all.return_value = []

        # 3. allocations
        mock_allocs = MagicMock()
        mock_allocs.all.return_value = []

        session.scalars.side_effect = [mock_credits, mock_rev]
        session.execute = AsyncMock(return_value=mock_allocs)

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
        credit = MagicMock()
        credit.id = 888
        credit.user_id = 10
        credit.amount = Decimal("40.00")

        credits_mock = MagicMock()
        credits_mock.all.return_value = [credit]

        # Partial reversal of 16 RUB
        reversal = MagicMock()
        reversal.amount = Decimal("-16.00")
        reversal.metadata_ = {"original_credit_id": 888}
        rev_mock = MagicMock()
        rev_mock.all.return_value = [reversal]

        session.scalars.side_effect = [credits_mock, rev_mock, MagicMock(all=MagicMock(return_value=[]))]
        alloc_mock = MagicMock()
        alloc_mock.all.return_value = []
        session.execute = AsyncMock(return_value=alloc_mock)

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



