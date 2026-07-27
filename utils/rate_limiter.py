import asyncio
import logging
import time
from collections import defaultdict

logger = logging.getLogger(__name__)


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
global_send_limiter = TokenBucketRateLimiter(
    rate=25.0,
    burst=25,
)


class PerUserRateLimiter:
    """Rate limiter для создания пользователей.
    
    Ограничивает количество попыток создания пользователя с одного telegram_id.
    Для личного проекта: максимум 3 попытки за 60 секунд.
    """
    
    def __init__(self, max_attempts: int = 3, window_seconds: int = 60):
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._attempts: dict[int, list[float]] = defaultdict(list)
        self._lock = asyncio.Lock()
    
    async def is_allowed(self, telegram_id: int) -> bool:
        """Проверяет, разрешено ли создание пользователя для данного telegram_id."""
        async with self._lock:
            now = time.time()
            cutoff = now - self.window_seconds
            
            # Очищаем старые попытки
            self._attempts[telegram_id] = [
                ts for ts in self._attempts[telegram_id]
                if ts > cutoff
            ]
            
            # Проверяем лимит
            if len(self._attempts[telegram_id]) >= self.max_attempts:
                logger.warning(
                    "Rate limit exceeded for user creation: telegram_id=%s, "
                    "attempts=%d/%d in %ds",
                    telegram_id,
                    len(self._attempts[telegram_id]),
                    self.max_attempts,
                    self.window_seconds,
                )
                return False
            
            # Записываем попытку
            self._attempts[telegram_id].append(now)
            return True
    
    def cleanup(self, telegram_id: int) -> None:
        """Очищает историю попыток для пользователя (после успешного создания)."""
        self._attempts.pop(telegram_id, None)


# Глобальный rate limiter для создания пользователей
user_creation_rate_limiter = PerUserRateLimiter(
    max_attempts=3,
    window_seconds=60,
)