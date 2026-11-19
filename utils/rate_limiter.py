import asyncio
import time


class TokenBucketRateLimiter:
    def __init__(self, rate: float = 25.0, burst: int = 25, parent: "TokenBucketRateLimiter | None" = None):
        self.rate = rate
        self.burst = burst
        self.tokens = float(burst)
        self.last_refill = time.monotonic()
        self._lock = asyncio.Lock()
        self._parent = parent

    async def acquire(self) -> None:
        while True:
            async with self._lock:
                now = time.monotonic()
                elapsed = now - self.last_refill

                self.tokens = min(
                    self.burst,
                    self.tokens + elapsed * self.rate,
                )

                self.last_refill = now

                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    break

                wait_time = (1.0 - self.tokens) / self.rate

            await asyncio.sleep(wait_time)
        if self._parent is not None:
            await self._parent.acquire()


# Shared Telegram Bot API ceiling across all workers and handlers (under 30 msg/s global limit)
_telegram_global_limiter = TokenBucketRateLimiter(
    rate=28.0,
    burst=28,
)

# Limiter for transactional notifications (topups, receipts, expiry alerts)
transactional_send_limiter = TokenBucketRateLimiter(
    rate=25.0,
    burst=25,
    parent=_telegram_global_limiter,
)

# Limiter for mass broadcasts and bulk notifications (keeps headroom for transactional traffic)
broadcast_send_limiter = TokenBucketRateLimiter(
    rate=18.0,
    burst=18,
    parent=_telegram_global_limiter,
)

# Backwards-compatible alias
global_send_limiter = transactional_send_limiter
