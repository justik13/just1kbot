import asyncio
import time


class TokenBucketRateLimiter:
    def __init__(self, rate: float = 25.0, burst: int = 25):
        self.rate = rate
        self.burst = burst
        self.tokens = float(burst)
        self.last_refill = time.monotonic()
        self._lock = asyncio.Lock()

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
                    return

                wait_time = (1.0 - self.tokens) / self.rate

            await asyncio.sleep(wait_time)


# Общий limiter для broadcast, notifications и других массовых отправок.
global_send_limiter = TokenBucketRateLimiter(rate=25.0, burst=25)