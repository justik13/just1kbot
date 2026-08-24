import asyncio
import time


class TokenBucketRateLimiter:
    def __init__(self, rate: float = 25.0, burst: int = 25):
        self.rate = rate
        self.burst = burst
        self.tokens = float(burst)
        self.last_refill = time.monotonic()
        self._lock: asyncio.Lock | None = None

    @property
    def lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def acquire(self) -> None:
        while True:
            async with self.lock:
                now = time.monotonic()
                elapsed = now - self.last_refill

                self.tokens = min(
                    self.burst,
                    self.tokens + elapsed * self.rate,
                )

                self.last_refill = now

                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return

                wait_time = (1.0 - self.tokens) / self.rate

            await asyncio.sleep(wait_time)


# Limiter для массовых рассылок (broadcasts)
broadcast_send_limiter = TokenBucketRateLimiter(
    rate=20.0,
    burst=20,
)

# Выделенный limiter для транзакционных уведомлений (пополнения, чеки)
transactional_send_limiter = TokenBucketRateLimiter(
    rate=25.0,
    burst=25,
)

# Совместимость
global_send_limiter = transactional_send_limiter