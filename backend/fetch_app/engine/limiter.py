"""Token-bucket bandwidth limiter shared by all of one user's transfers."""

from __future__ import annotations

import asyncio
import time


class RateLimiter:
    def __init__(self, rate_bps: int = 0) -> None:
        self.rate = max(0, int(rate_bps))
        self._tokens = float(self.rate)
        self._stamp = time.monotonic()
        self._lock = asyncio.Lock()

    def set_rate(self, rate_bps: int) -> None:
        self.rate = max(0, int(rate_bps))
        self._tokens = min(self._tokens, float(self.rate))

    async def consume(self, amount: int) -> None:
        """Wait until ``amount`` bytes may pass. No-op when unlimited."""
        if self.rate <= 0 or amount <= 0:
            return
        async with self._lock:
            remaining = float(amount)
            while remaining > 0:
                rate = self.rate
                if rate <= 0:
                    return
                now = time.monotonic()
                # Allow at most one second of burst.
                self._tokens = min(float(rate), self._tokens + (now - self._stamp) * rate)
                self._stamp = now
                if self._tokens >= 1:
                    used = min(self._tokens, remaining)
                    self._tokens -= used
                    remaining -= used
                if remaining > 0:
                    await asyncio.sleep(min(remaining, rate) / rate if self._tokens < 1 else 0)
