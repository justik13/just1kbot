from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

from services.referral_bonus import calculate_referral_bonus


def test_referral_bonus_is_ten_percent_for_every_purchase_amount():
    assert calculate_referral_bonus(1) == Decimal("0")
    assert calculate_referral_bonus(10) == Decimal("1")
    assert calculate_referral_bonus(30) == Decimal("3")
    assert calculate_referral_bonus(99) == Decimal("9")
    assert calculate_referral_bonus(100) == Decimal("10")
    assert calculate_referral_bonus(999) == Decimal("99")
    assert calculate_referral_bonus(1000) == Decimal("100")


def test_referral_bonus_has_no_duration_or_first_purchase_gate():
    # The calculation depends only on the successfully spent amount.
    for purchase_amount in (10, 50, 123, 500, 999):
        assert calculate_referral_bonus(purchase_amount) == (
            Decimal(str(purchase_amount)) * Decimal("0.10")
        ).quantize(Decimal("1"), rounding="ROUND_DOWN")


def test_referral_bonus_rejects_non_positive_amounts():
    assert calculate_referral_bonus(0) == Decimal("0")
    assert calculate_referral_bonus(-100) == Decimal("0")


class TestReferralBonusLedgerEntryShape:
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
        from services.referral_bonus import grant_referral_bonus_for_topup
        from database.models import AccountLedgerEntry

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
        assert entry.amount == Decimal("3")
        assert entry.metadata_["topup_payment_id"] == 42, \
            "topup_payment_id should be preserved in metadata for traceability"

    def test_reverse_referral_bonus_for_topup(self):
        import asyncio
        from services.referral_bonus import reverse_referral_bonus_for_topup
        from database.models import AccountLedgerEntry

        captured_entry = {}

        existing_bonus_credit = MagicMock()
        existing_bonus_credit.id = 100
        existing_bonus_credit.user_id = 1
        existing_bonus_credit.amount = Decimal("10")
        existing_bonus_credit.metadata_ = {"topup_payment_id": 42, "source_type": "referral_bonus"}

        def fake_add(entry):
            if isinstance(entry, AccountLedgerEntry):
                captured_entry["obj"] = entry

        payment = MagicMock()
        payment.user_id = 4

        purchaser = MagicMock()
        purchaser.id = 4
        purchaser.referred_by = 111

        referrer = MagicMock()
        referrer.id = 1

        def fake_get(model, pk):
            if pk == 42:
                return payment
            if pk == 4:
                return purchaser
            return None

        scalars_mock = MagicMock()
        scalars_mock.all.return_value = [existing_bonus_credit]

        session = AsyncMock()
        session.get = AsyncMock(side_effect=fake_get)
        session.scalar = AsyncMock(side_effect=[referrer, None])
        session.scalars = AsyncMock(return_value=scalars_mock)
        session.add = fake_add
        session.flush = AsyncMock()

        reversed_amount = asyncio.run(
            reverse_referral_bonus_for_topup(session, payment_id=42)
        )

        assert reversed_amount == Decimal("10")
        entry = captured_entry.get("obj")
        assert entry is not None
        assert entry.user_id == 1
        assert entry.amount == Decimal("-10")
        assert entry.reversal_of_id is None
        assert entry.payment_id is None


def test_first_topup_bonus_credits_both_purchaser_and_referrer():
    import asyncio
    from services.referral_bonus import grant_referral_bonus_for_topup
    from database.models import AccountLedgerEntry

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
    assert referrer_entry.amount == Decimal("50")

    assert purchaser_entry.user_id == 20
    assert purchaser_entry.amount == Decimal("50")
    assert purchaser_entry.metadata_["reason"] == "first_topup_welcome"


def test_second_topup_credits_only_referrer():
    import asyncio
    from services.referral_bonus import grant_referral_bonus_for_topup
    from database.models import AccountLedgerEntry

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
    assert referrer_entry.amount == Decimal("100")


