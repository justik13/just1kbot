from sqlalchemy.ext.asyncio import AsyncSession
from database.models import PaidValueLedgerEntry


async def add_entry(session: AsyncSession, **values) -> PaidValueLedgerEntry:
    """Append only; database indexes provide callback/retry idempotency."""
    entry = PaidValueLedgerEntry(**values)
    session.add(entry)
    await session.flush()
    return entry
