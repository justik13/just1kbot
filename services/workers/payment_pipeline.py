"""Bounded durable payment workers; each claim is committed before network work."""
import asyncio, uuid
from database.connection import session_scope
from services import payment_provider_operations as provider
from services import payment_fulfillment as fulfillment
from services.workers import webhook_inbox
async def run_once():
    worker=uuid.uuid4().hex
    for module in (provider,webhook_inbox,fulfillment):
        async with session_scope() as session:
            await module.recover_stale(session); item=await module.claim(session,worker); await session.commit()
        if item:
            async with session_scope() as session:
                fresh=await session.get(type(item),item.id)
                if getattr(fresh,"locked_by",None)==worker:
                    if module is webhook_inbox: await module.process(session,fresh)
                    else: await module.execute(session,fresh)
                    await session.commit()
async def run_forever(interval=2):
    while True:
        await run_once(); await asyncio.sleep(interval)
