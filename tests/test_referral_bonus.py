import unittest

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

from services.referral_bonus import calculate_referral_bonus



class TestReferralBonusCalculation(unittest.TestCase):
    def test_referral_bonus_is_ten_percent_for_every_purchase_amount(self):
        assert calculate_referral_bonus(1) == Decimal(0)
        assert calculate_referral_bonus(10) == Decimal(1)
        assert calculate_referral_bonus(30) == Decimal(3)
        assert calculate_referral_bonus(99) == Decimal(9)
        assert calculate_referral_bonus(100) == Decimal(10)
        assert calculate_referral_bonus(999) == Decimal(99)
        assert calculate_referral_bonus(1000) == Decimal(100)


    def test_referral_bonus_has_no_duration_or_first_purchase_gate(self):
        # The calculation depends only on the successfully spent amount.
        for purchase_amount in (10, 50, 123, 500, 999):
            assert calculate_referral_bonus(purchase_amount) == (
                Decimal(str(purchase_amount)) * Decimal("0.10")
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
        assert entry.amount == Decimal(3)
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

    def test_reverse_referral_bonus_with_invalid_uuid_returns_zero(self):
        import asyncio
        from services.referral_bonus import reverse_referral_bonus_for_topup

        session = AsyncMock()
        with self.assertRaises(ValueError) as cm:
            asyncio.run(
                reverse_referral_bonus_for_topup(session, order_id="invalid-not-a-uuid")
            )
        self.assertIn("Invalid order_id for referral reversal", str(cm.exception))

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
        assert referrer_entry.amount == Decimal(50)

        assert purchaser_entry.user_id == 20
        assert purchaser_entry.amount == Decimal(50)
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
        assert referrer_entry.amount == Decimal(100)


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
    
        # Welcome bonus MUST be granted (10% of 100 = 10)
        assert res.purchaser_welcome_bonus == Decimal(10)

