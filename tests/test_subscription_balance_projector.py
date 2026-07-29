import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from services.subscription_balance_projector import (
    EntitlementEvent, LedgerEntry, PaymentSnapshot, TariffVersionSnapshot,
    project_subscription_balance,
)

UTC = timezone.utc
T0 = datetime(2026, 1, 1, tzinfo=UTC)


class SubscriptionBalanceProjectorTests(unittest.TestCase):
    def paid(self, *, hours=24, value="24", start=T0, as_of=T0, end=None,
             event_user=1, ledger_user=1, payment_user=1, payment_id=10,
             ledger_hours=None, version=100, extra_events=(), extra_ledger=()):
        event = EntitlementEvent(1, event_user, "payment", str(payment_id), "payment_grant", hours, start)
        item = LedgerEntry(11, ledger_user, "confirmed_payment", ledger_hours or hours,
                           Decimal(value), "RUB", version, payment_id)
        payment = PaymentSnapshot(payment_id, payment_user, version, version, Decimal(value),
                                  "RUB", hours, Decimal(value), "RUB")
        tariff = TariffVersionSnapshot(version, version, hours, Decimal(value), "RUB")
        events=(event,) + tuple(extra_events)
        coverage=end if end is not None else start + timedelta(hours=hours)
        return project_subscription_balance(as_of=as_of, subscription_end=coverage,
            entitlement_events=events, ledger_entries=(item,) + tuple(extra_ledger),
            tariff_versions={version: tariff}, payments={payment_id: payment})

    def test_new_purchase_is_full_paid_lot(self):
        s=self.paid(); self.assertEqual((s.tracked,s.remaining_paid_hours,s.remaining_paid_value_rub),(True,24,Decimal("24")))

    def test_partially_used_paid_lot(self):
        s=self.paid(as_of=T0+timedelta(hours=4)); self.assertEqual((s.remaining_paid_hours,s.remaining_paid_value_rub),(20,Decimal("20")))

    def test_started_hour_is_consumed(self):
        s=self.paid(as_of=T0+timedelta(seconds=1)); self.assertEqual(s.remaining_paid_hours,23)

    def test_rounding_loss_is_less_than_hour(self):
        s=self.paid(as_of=T0+timedelta(minutes=30)); self.assertGreater(s.rounding_loss_hours,0); self.assertLess(s.rounding_loss_hours,1)

    def test_expired_paid_lot(self):
        s=self.paid(as_of=T0+timedelta(days=2),end=T0+timedelta(hours=24)); self.assertEqual((s.remaining_paid_hours,s.remaining_paid_value_rub),(0,Decimal(0)))

    def test_future_renewal_is_full(self):
        s=self.paid(start=T0+timedelta(hours=2),as_of=T0); self.assertEqual(s.remaining_paid_hours,24)

    def test_two_paid_lots_different_versions(self):
        e2=EntitlementEvent(2,1,"payment","20","payment_grant",48,T0+timedelta(hours=1))
        l2=LedgerEntry(12,1,"confirmed_payment",48,Decimal("96"),"RUB",200,20)
        p={10:PaymentSnapshot(10,1,100,100,Decimal(24),"RUB",24,Decimal(24),"RUB"),20:PaymentSnapshot(20,1,200,200,Decimal(96),"RUB",48,Decimal(96),"RUB")}
        v={100:TariffVersionSnapshot(100,100,24,Decimal(24),"RUB"),200:TariffVersionSnapshot(200,200,48,Decimal(96),"RUB")}
        s=project_subscription_balance(as_of=T0,subscription_end=T0+timedelta(hours=72),entitlement_events=(EntitlementEvent(1,1,"payment","10","payment_grant",24,T0),e2),ledger_entries=(LedgerEntry(11,1,"confirmed_payment",24,Decimal(24),"RUB",100,10),l2),tariff_versions=v,payments=p)
        self.assertEqual((len(s.paid_lots),s.remaining_paid_value_rub),(2,Decimal(120)))

    def test_paid_plus_referral_bonus(self):
        bonus=EntitlementEvent(2,1,"payment","10","referral_user_bonus",5,T0)
        s=self.paid(extra_events=(bonus,),end=T0+timedelta(hours=29)); self.assertEqual((s.remaining_paid_hours,s.remaining_bonus_hours),(24,5))

    def test_bonus_value_is_zero(self):
        bonus=EntitlementEvent(2,1,"payment","10","referral_user_bonus",5,T0)
        s=self.paid(extra_events=(bonus,),end=T0+timedelta(hours=29)); self.assertEqual(s.bonus_lots[0].paid_value_rub,0)

    def test_multiple_bonus_lots(self):
        b=(EntitlementEvent(2,1,"payment","10","referral_user_bonus",5,T0),EntitlementEvent(3,1,"manual","x","manual_grant",3,T0))
        s=self.paid(extra_events=b,end=T0+timedelta(hours=32)); self.assertEqual((len(s.bonus_lots),s.remaining_bonus_hours),(2,8))

    def test_gap_between_grants(self):
        b=EntitlementEvent(2,1,"manual","x","manual_grant",3,T0+timedelta(hours=30))
        s=self.paid(extra_events=(b,),end=T0+timedelta(hours=33)); self.assertEqual(s.bonus_lots[0].segment_start,T0+timedelta(hours=30))

    def test_partial_payment_reversal_is_rejected(self):
        reversal=EntitlementEvent(2,1,"payment","10","payment_reversal",-4,T0+timedelta(hours=1),1)
        lr=LedgerEntry(12,1,"payment_reversal",-24,Decimal("-24"),"RUB",100,10,11)
        s=self.paid(extra_events=(reversal,),extra_ledger=(lr,),end=T0+timedelta(hours=20)); self.assertEqual(s.failure_code,"reversal_amount_mismatch")

    def test_partial_referral_reversal_is_rejected(self):
        b=EntitlementEvent(2,1,"payment","10","referral_user_bonus",5,T0)
        r=EntitlementEvent(3,1,"payment","10","referral_reversal",-3,T0+timedelta(hours=1),2)
        s=self.paid(extra_events=(b,r),end=T0+timedelta(hours=26)); self.assertEqual(s.failure_code,"reversal_amount_mismatch")

    def test_partial_reversal_does_not_span_segments(self):
        b=EntitlementEvent(2,1,"manual","x","manual_grant",5,T0)
        r=EntitlementEvent(3,1,"payment","10","payment_reversal",-7,T0+timedelta(hours=1),1)
        lr=LedgerEntry(12,1,"payment_reversal",-24,Decimal("-24"),"RUB",100,10,11)
        s=self.paid(extra_events=(b,r),extra_ledger=(lr,),end=T0+timedelta(hours=22)); self.assertEqual(s.failure_code,"reversal_amount_mismatch")

    def test_reversal_exceeds_balance_fails(self):
        r=EntitlementEvent(2,1,"payment","10","payment_reversal",-24,T0+timedelta(hours=25),1)
        lr=LedgerEntry(12,1,"payment_reversal",-24,Decimal(-24),"RUB",100,10,11)
        s=self.paid(extra_events=(r,),extra_ledger=(lr,)); self.assertEqual((s.tracked,s.failure_code),(False,"reversal_exceeds_balance"))

    def test_paid_grant_without_ledger_fails(self):
        e=EntitlementEvent(1,1,"payment","10","payment_grant",24,T0)
        s=project_subscription_balance(as_of=T0,subscription_end=T0+timedelta(hours=24),entitlement_events=(e,),ledger_entries=(),tariff_versions={},payments={})
        self.assertEqual((s.tracked,s.failure_code,s.remaining_paid_value_rub),(False,"paid_grant_without_value_ledger",None))

    def test_mismatched_paid_hours_fails(self): self.assertFalse(self.paid(ledger_hours=23).tracked)
    def test_mismatched_user_or_payment_fails(self): self.assertFalse(self.paid(ledger_user=2).tracked)

    def test_ledger_reversal_without_entitlement_fails(self):
        lr=LedgerEntry(12,1,"payment_reversal",-24,Decimal(-24),"RUB",100,10,11)
        s=self.paid(extra_ledger=(lr,)); self.assertEqual(s.failure_code,"ledger_reversal_without_entitlement_reversal")

    def test_active_subscription_end_mismatch_fails(self):
        s=self.paid(end=T0+timedelta(hours=25)); self.assertEqual(s.failure_code,"subscription_end_projection_mismatch")

    def test_expired_legacy_user_returns_zero(self):
        e=EntitlementEvent(1,1,"payment","10","payment_grant",24,T0)
        s=project_subscription_balance(as_of=T0+timedelta(days=2),subscription_end=T0+timedelta(days=1),entitlement_events=(e,),ledger_entries=(),tariff_versions={},payments={})
        self.assertTrue(s.tracked); self.assertEqual(s.remaining_paid_value_rub,0)

    def test_active_legacy_user_fails(self): self.test_paid_grant_without_ledger_fails()

    def test_property_matrix_values(self):
        for hours in (1,7,24,720):
            for value in ("1","99.99","1234.56"):
                for used in (0,hours//2,hours):
                    s=self.paid(hours=hours,value=value,as_of=T0+timedelta(hours=used))
                    self.assertGreaterEqual(s.remaining_paid_value_rub,0)

    def test_property_matrix_value_never_increases(self):
        for hours in (2,13,100):
            prior=Decimal("100.01")
            for used in range(hours+1):
                value=self.paid(hours=hours,value="100.01",as_of=T0+timedelta(hours=used)).remaining_paid_value_rub
                self.assertLessEqual(value,prior); prior=value

    def test_latest_full_reversal_removes_value(self):
        r=EntitlementEvent(2,1,"payment","10","payment_reversal",-24,T0,1); lr=LedgerEntry(12,1,"payment_reversal",-24,Decimal(-24),"RUB",100,10,11)
        s=self.paid(extra_events=(r,),extra_ledger=(lr,),end=T0); self.assertEqual((s.tracked,s.remaining_paid_value_rub),(True,Decimal(0)))

    def test_own_referral_reversal_batch(self):
        b=EntitlementEvent(2,1,"payment","10","referral_user_bonus",5,T0); r1=EntitlementEvent(3,1,"payment","10","payment_reversal",-24,T0,1); r2=EntitlementEvent(4,1,"payment","10","referral_reversal",-5,T0,2); lr=LedgerEntry(12,1,"payment_reversal",-24,Decimal(-24),"RUB",100,10,11)
        s=self.paid(extra_events=(b,r1,r2),extra_ledger=(lr,),end=T0); self.assertEqual((s.tracked,s.remaining_paid_value_rub,s.remaining_bonus_hours),(True,Decimal(0),0))

    def test_reversal_crosses_unrelated_bonus(self):
        b=EntitlementEvent(2,1,"manual","x","manual_grant",5,T0); r=EntitlementEvent(3,1,"payment","10","payment_reversal",-24,T0,1); lr=LedgerEntry(12,1,"payment_reversal",-24,Decimal(-24),"RUB",100,10,11)
        self.assertEqual(self.paid(extra_events=(b,r),extra_ledger=(lr,)).failure_code,"reversal_crosses_unrelated_segments")

    def test_old_payment_reversal_crosses_newer_payment(self):
        events=(EntitlementEvent(1,1,"payment","10","payment_grant",24,T0),EntitlementEvent(2,1,"payment","20","payment_grant",24,T0),EntitlementEvent(3,1,"payment","10","payment_reversal",-24,T0,1))
        ledger=(LedgerEntry(11,1,"confirmed_payment",24,Decimal(24),"RUB",100,10),LedgerEntry(12,1,"confirmed_payment",24,Decimal(48),"RUB",200,20),LedgerEntry(13,1,"payment_reversal",-24,Decimal(-24),"RUB",100,10,11))
        payments={10:PaymentSnapshot(10,1,100,100,Decimal(24),"RUB",24,Decimal(24),"RUB"),20:PaymentSnapshot(20,1,200,200,Decimal(48),"RUB",24,Decimal(48),"RUB")}
        versions={100:TariffVersionSnapshot(100,100,24,Decimal(24),"RUB"),200:TariffVersionSnapshot(200,200,24,Decimal(48),"RUB")}
        s=project_subscription_balance(as_of=T0,subscription_end=T0+timedelta(hours=24),entitlement_events=events,ledger_entries=ledger,tariff_versions=versions,payments=payments)
        self.assertEqual((s.failure_code,s.remaining_paid_value_rub),("reversal_crosses_unrelated_segments",None))

    def test_orphan_confirmed_ledger(self):
        orphan=LedgerEntry(99,1,"confirmed_payment",1,Decimal(1),"RUB",100,99)
        self.assertEqual(self.paid(extra_ledger=(orphan,)).failure_code,"confirmed_payment_without_entitlement_grant")

    def test_duplicate_grant_for_confirmed_ledger(self):
        duplicate=EntitlementEvent(2,1,"payment","10","payment_grant",24,T0)
        self.assertEqual(self.paid(extra_events=(duplicate,),end=T0+timedelta(hours=48)).failure_code,"confirmed_payment_without_entitlement_grant")

    def test_unsupported_economic_entry(self):
        item=LedgerEntry(99,1,"manual_adjustment",1,Decimal(1),"RUB",100,None)
        self.assertEqual(self.paid(extra_ledger=(item,)).failure_code,"unsupported_paid_value_entry")

    def test_inactive_and_null_with_remaining_time(self):
        self.assertEqual(self.paid(end=T0-timedelta(seconds=1)).failure_code,"subscription_end_projection_mismatch")
        e=EntitlementEvent(1,1,"payment","10","payment_grant",24,T0); l=LedgerEntry(11,1,"confirmed_payment",24,Decimal(24),"RUB",100,10); p=PaymentSnapshot(10,1,100,100,Decimal(24),"RUB",24,Decimal(24),"RUB"); v=TariffVersionSnapshot(100,100,24,Decimal(24),"RUB")
        s=project_subscription_balance(as_of=T0,subscription_end=None,entitlement_events=(e,),ledger_entries=(l,),tariff_versions={100:v},payments={10:p}); self.assertEqual(s.failure_code,"subscription_end_projection_mismatch")

    def test_rounding_uses_exact_microseconds(self):
        s=self.paid(as_of=T0+timedelta(microseconds=1)); self.assertEqual(s.rounding_loss_hours,Decimal(3_599_999_999)/Decimal(3_600_000_000))

    def assert_invalid_shape(self, event):
        s=project_subscription_balance(as_of=T0,subscription_end=None,
            entitlement_events=(event,),ledger_entries=(),tariff_versions={},payments={})
        self.assertEqual((s.tracked,s.failure_code,s.remaining_paid_value_rub),
                         (False,"invalid_entitlement_shape",None))

    def test_positive_payment_reversal_has_invalid_shape(self):
        self.assert_invalid_shape(EntitlementEvent(1,1,"payment","1","payment_reversal",1,T0,2))

    def test_positive_referral_reversal_has_invalid_shape(self):
        self.assert_invalid_shape(EntitlementEvent(1,1,"payment","1","referral_reversal",1,T0,2))

    def test_negative_payment_grant_has_invalid_shape(self):
        self.assert_invalid_shape(EntitlementEvent(1,1,"payment","1","payment_grant",-1,T0))

    def test_negative_referral_bonus_has_invalid_shape(self):
        self.assert_invalid_shape(EntitlementEvent(1,1,"payment","1","referral_user_bonus",-1,T0))

    def test_zero_grant_has_invalid_shape(self):
        self.assert_invalid_shape(EntitlementEvent(1,1,"manual","1","manual_grant",0,T0))

    def test_reversal_without_original_has_invalid_shape(self):
        self.assert_invalid_shape(EntitlementEvent(1,1,"payment","1","payment_reversal",-1,T0))

    def test_grant_with_reversed_entry_has_invalid_shape(self):
        self.assert_invalid_shape(EntitlementEvent(1,1,"payment","1","payment_grant",1,T0,2))

    def test_valid_shapes_continue_to_project(self):
        self.assertTrue(self.paid().tracked)
        reversal=EntitlementEvent(2,1,"payment","10","payment_reversal",-24,T0,1)
        ledger_reversal=LedgerEntry(12,1,"payment_reversal",-24,Decimal(-24),"RUB",100,10,11)
        self.assertTrue(self.paid(extra_events=(reversal,),extra_ledger=(ledger_reversal,),end=T0).tracked)


if __name__ == "__main__": unittest.main()
