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
        session.scalar = AsyncMock(side_effect=[purchaser, referrer, None])
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

        scalars_mock = MagicMock()
        scalars_mock.all.return_value = [existing_bonus_credit]

        session = AsyncMock()
        session.scalars = AsyncMock(return_value=scalars_mock)
        session.scalar = AsyncMock(return_value=None)
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
        assert entry.reversal_of_id == 100
        assert entry.payment_id is None

