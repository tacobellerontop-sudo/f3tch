"""In-memory login throttling (per client address and per username)."""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class LoginRateLimiter:
    def __init__(self) -> None:
        self._failures: dict[str, deque[float]] = defaultdict(deque)
        self._locked_until: dict[str, float] = {}
        self._lock = threading.Lock()

    def retry_after(self, keys: list[str]) -> int:
        """Seconds until any of ``keys`` may try again (0 when not blocked)."""
        now = time.monotonic()
        with self._lock:
            remaining = max((self._locked_until.get(k, 0) - now for k in keys), default=0)
        return max(0, int(remaining + 0.999))

    def record_failure(
        self, keys: list[str], *, max_attempts: int, window_seconds: int, lockout_seconds: int
    ) -> None:
        now = time.monotonic()
        with self._lock:
            for key in keys:
                bucket = self._failures[key]
                bucket.append(now)
                while bucket and now - bucket[0] > window_seconds:
                    bucket.popleft()
                if len(bucket) >= max_attempts:
                    self._locked_until[key] = now + lockout_seconds
                    bucket.clear()
            if len(self._failures) > 10_000:  # bound memory under abuse
                self._failures.clear()

    def record_success(self, keys: list[str]) -> None:
        with self._lock:
            for key in keys:
                self._failures.pop(key, None)
                self._locked_until.pop(key, None)

    def reset(self) -> None:
        with self._lock:
            self._failures.clear()
            self._locked_until.clear()
