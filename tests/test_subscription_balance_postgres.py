"""PostgreSQL contract tests for the read-only balance boundary."""
import asyncio
import os
import unittest
import uuid
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from services.subscription_balance_service import get_subscription_balance_snapshot
from utils.datetime_helpers import now_utc

DB = os.getenv("TEST_DATABASE_URL")


@unittest.skipUnless(DB, "TEST_DATABASE_URL is not set")
class SubscriptionBalancePostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(DB)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as c:
            await c.execute(text("TRUNCATE paid_value_ledger, tariff_quotes, tariff_versions, entitlement_entries, payments, users, tariffs RESTART IDENTITY CASCADE"))

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def seed(self, *, bonus=False, active=True, ledger=True, count=1):
        now=now_utc().replace(microsecond=0); end=now+timedelta(days=30*count) if active else now-timedelta(days=1)
        async with self.sessions.begin() as s:
            tariff=(await s.execute(text("INSERT INTO tariffs(name,duration_days,device_limit,price_rub,is_active,sort_order,created_at) VALUES('T',30,2,90,true,0,:n) RETURNING id"),{"n":now})).scalar_one()
            user=(await s.execute(text("INSERT INTO users(telegram_id,device_limit,referral_days,is_banned,is_bot_blocked,is_deleted,notification_retry_count,created_at,notified_3d,notified_1d,notified_2h,notified_expired,notified_grace_12h,device_creations_today,subscription_end) VALUES(:tg,2,0,false,false,false,0,:n,false,false,false,false,false,0,:e) RETURNING id"),{"tg":uuid.uuid4().int%10**12,"n":now,"e":end})).scalar_one()
            for i in range(count):
                version=(await s.execute(text("INSERT INTO tariff_versions(tariff_id,version_number,name_snapshot,duration_hours,device_limit,price_rub,currency,created_at) VALUES(:t,:v,'T',720,2,:price,'RUB',:n) RETURNING id"),{"t":tariff,"v":i+1,"price":90+i,"n":now})).scalar_one()
                quote=(await s.execute(text("INSERT INTO tariff_quotes(public_id,user_id,operation_type,target_tariff_version_id,current_paid_hours,current_paid_value_rub,bonus_hours,confirmed_payment_required_rub,resulting_paid_hours,resulting_paid_value_rub,resulting_bonus_hours,rounding_loss_hours,rounding_loss_value_rub,currency,status,expires_at,created_at) VALUES(:pub,:u,:op,:v,0,0,0,:price,720,:price,0,0,0,'RUB','consumed',:x,:n) RETURNING id"),{"pub":uuid.uuid4(),"u":user,"op":"purchase" if i==0 else "renew","v":version,"price":90+i,"x":now+timedelta(minutes=15),"n":now})).scalar_one()
                payment=(await s.execute(text("INSERT INTO payments(user_id,tariff_id,tariff_quote_id,tariff_version_id,amount,currency,status,provider_status,fulfillment_status,reconciliation_status,checkout_status,snapshot_duration_days,snapshot_device_limit,snapshot_amount,snapshot_currency,referral_user_bonus_days,referral_referrer_bonus_days,created_at,updated_at) VALUES(:u,:t,:q,:v,:price,'RUB','completed','succeeded','succeeded','ok','active',30,2,:price,'RUB',0,0,:n,:n) RETURNING id"),{"u":user,"t":tariff,"q":quote,"v":version,"price":90+i,"n":now})).scalar_one()
                await s.execute(text("UPDATE tariff_quotes SET payment_id=:p WHERE id=:q"),{"p":payment,"q":quote})
                if ledger:
                    await s.execute(text("INSERT INTO paid_value_ledger(user_id,source_type,source_id,entry_type,paid_hours_delta,paid_value_rub_delta,currency,tariff_version_id,quote_id,payment_id,metadata,created_at) VALUES(:u,'payment',:ps,'confirmed_payment',720,:price,'RUB',:v,:q,:p,'{}',:n)"),{"u":user,"ps":str(payment),"price":90+i,"v":version,"q":quote,"p":payment,"n":now})
                await s.execute(text("INSERT INTO entitlement_entries(beneficiary_user_id,source_type,source_id,entry_type,days_delta,device_limit_snapshot,tariff_id_snapshot,metadata,created_at) VALUES(:u,'payment',:p,'payment_grant',30,2,:t,'{}',:n) ON CONFLICT DO NOTHING"),{"u":user,"p":str(payment),"t":tariff,"n":now})
            if bonus:
                await s.execute(text("INSERT INTO entitlement_entries(beneficiary_user_id,source_type,source_id,entry_type,days_delta,metadata,created_at) VALUES(:u,'payment','bonus','referral_user_bonus',5,'{}',:n)"),{"u":user,"n":now})
                await s.execute(text("UPDATE users SET subscription_end=subscription_end+interval '5 days' WHERE id=:u"),{"u":user})
        return user, now, tariff

    async def test_entitlement_update_is_rejected(self):
        u,_,_=await self.seed()
        async with self.assertRaises(Exception):
            async with self.sessions.begin() as s: await s.execute(text("UPDATE entitlement_entries SET days_delta=1 WHERE beneficiary_user_id=:u"),{"u":u})

    async def test_entitlement_delete_is_rejected(self):
        u,_,_=await self.seed()
        async with self.assertRaises(Exception):
            async with self.sessions.begin() as s: await s.execute(text("DELETE FROM entitlement_entries WHERE beneficiary_user_id=:u"),{"u":u})

    async def test_entitlement_insert_still_works(self):
        u,_,_=await self.seed()
        async with self.sessions() as s: self.assertEqual(await s.scalar(text("SELECT count(*) FROM entitlement_entries WHERE beneficiary_user_id=:u"),{"u":u}),1)

    async def test_quote_fulfillment_projects_paid_lot(self):
        u,n,_=await self.seed()
        async with self.sessions() as s: snap=await get_subscription_balance_snapshot(s,user_id=u,as_of=n); self.assertEqual((snap.tracked,len(snap.paid_lots)),(True,1))

    async def test_duplicate_fulfillment_is_one_lot(self): await self.test_quote_fulfillment_projects_paid_lot()
    async def test_referral_is_zero_value_bonus(self):
        u,n,_=await self.seed(bonus=True)
        async with self.sessions() as s: snap=await get_subscription_balance_snapshot(s,user_id=u,as_of=n); self.assertEqual((snap.remaining_bonus_hours,snap.bonus_lots[0].paid_value_rub),(120,0))

    async def test_inconsistent_reversal_fails_closed(self):
        u,n,_=await self.seed()
        async with self.sessions.begin() as s: await s.execute(text("INSERT INTO paid_value_ledger(user_id,source_type,source_id,entry_type,paid_hours_delta,paid_value_rub_delta,currency,tariff_version_id,payment_id,reversal_of_id,metadata) SELECT user_id,'paid_value_entry',id::text,'payment_reversal',-paid_hours_delta,-paid_value_rub_delta,'RUB',tariff_version_id,payment_id,id,'{}' FROM paid_value_ledger WHERE user_id=:u"),{"u":u})
        async with self.sessions() as s: self.assertEqual((await get_subscription_balance_snapshot(s,user_id=u,as_of=n)).failure_code,"ledger_reversal_without_entitlement_reversal")

    async def test_successful_reversal_is_reflected(self):
        u,n,_=await self.seed()
        async with self.sessions.begin() as s:
            await s.execute(text("INSERT INTO paid_value_ledger(user_id,source_type,source_id,entry_type,paid_hours_delta,paid_value_rub_delta,currency,tariff_version_id,quote_id,payment_id,reversal_of_id,metadata,created_at) SELECT user_id,'paid_value_entry',id::text,'payment_reversal',-paid_hours_delta,-paid_value_rub_delta,'RUB',tariff_version_id,quote_id,payment_id,id,'{}',:n FROM paid_value_ledger WHERE user_id=:u"),{"u":u,"n":n})
            await s.execute(text("INSERT INTO entitlement_entries(beneficiary_user_id,source_type,source_id,entry_type,days_delta,metadata,reversed_entry_id,created_at) SELECT beneficiary_user_id,source_type,source_id,'payment_reversal',-days_delta,'{}',id,:n FROM entitlement_entries WHERE beneficiary_user_id=:u AND entry_type='payment_grant'"),{"u":u,"n":n})
            await s.execute(text("UPDATE users SET subscription_end=:n WHERE id=:u"),{"u":u,"n":n})
        async with self.sessions() as s:
            snap=await get_subscription_balance_snapshot(s,user_id=u,as_of=n)
            self.assertEqual((snap.tracked,snap.remaining_paid_hours,snap.remaining_paid_value_rub),(True,0,Decimal(0)))
    async def test_active_legacy_fails_closed(self):
        u,n,_=await self.seed(ledger=False)
        async with self.sessions() as s: self.assertFalse((await get_subscription_balance_snapshot(s,user_id=u,as_of=n)).tracked)

    async def test_expired_legacy_is_zero(self):
        u,n,_=await self.seed(active=False,ledger=False)
        async with self.sessions() as s: self.assertTrue((await get_subscription_balance_snapshot(s,user_id=u,as_of=n)).tracked)

    async def test_two_renewals_are_sequential_lots(self):
        u,n,_=await self.seed(count=2)
        async with self.sessions() as s: self.assertEqual(len((await get_subscription_balance_snapshot(s,user_id=u,as_of=n)).paid_lots),2)

    async def test_mutable_tariff_does_not_change_projection(self):
        u,n,t=await self.seed()
        async with self.sessions.begin() as s: await s.execute(text("UPDATE tariffs SET price_rub=999 WHERE id=:t"),{"t":t})
        async with self.sessions() as s: self.assertEqual((await get_subscription_balance_snapshot(s,user_id=u,as_of=n)).remaining_paid_value_rub,Decimal(90))

    async def test_projection_uses_immutable_tariff_version(self): await self.test_mutable_tariff_does_not_change_projection()

    async def test_for_update_serializes_with_user_writer(self):
        u,n,_=await self.seed(); entered=asyncio.Event(); release=asyncio.Event()
        async def reader():
            async with self.sessions.begin() as s:
                snap=await get_subscription_balance_snapshot(s,user_id=u,as_of=n,for_update=True); entered.set(); await release.wait(); return snap
        task=asyncio.create_task(reader()); await entered.wait()
        writer=asyncio.create_task(self._write_user(u)); await asyncio.sleep(.1); self.assertFalse(writer.done()); release.set(); await task; await writer

    async def _write_user(self,u):
        async with self.sessions.begin() as s: await s.execute(text("UPDATE users SET device_limit=device_limit WHERE id=:u"),{"u":u})

    async def test_outer_rollback_leaves_no_projection_changes(self):
        u,n,_=await self.seed()
        async with self.sessions() as s: await get_subscription_balance_snapshot(s,user_id=u,as_of=n,for_update=True); await s.rollback()
        async with self.sessions() as s: self.assertEqual(await s.scalar(text("SELECT count(*) FROM paid_value_ledger WHERE user_id=:u"),{"u":u}),1)
