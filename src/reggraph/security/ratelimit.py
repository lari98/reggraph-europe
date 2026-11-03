"""Token-bucket rate limiter (in-process).

Production note: the first line of defence is the AWS WAF / API gateway. This limiter is the
second line and keeps the service safe when run on its own. The key table is size-capped so an
attacker cannot exhaust memory by rotating keys.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass

from reggraph.core.clock import Clock


@dataclass
class _Bucket:
    tokens: float
    updated: float


class RateLimiter:
    def __init__(
        self, clock: Clock, *, per_minute: int = 120, burst: int = 30, max_keys: int = 10_000
    ) -> None:
        self._clock = clock
        self._rate = per_minute / 60.0
        self._burst = float(burst)
        self._max_keys = max_keys
        self._buckets: OrderedDict[str, _Bucket] = OrderedDict()

    def size(self) -> int:
        """Number of clients currently tracked (bounded by ``max_keys``)."""
        return len(self._buckets)

    def allow(self, key: str) -> bool:
        now = self._clock.now().timestamp()
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = _Bucket(self._burst, now)
            self._buckets[key] = bucket
            if len(self._buckets) > self._max_keys:
                self._buckets.popitem(last=False)  # drop the least recently used key
        else:
            self._buckets.move_to_end(key)
        elapsed = max(0.0, now - bucket.updated)
        bucket.tokens = min(self._burst, bucket.tokens + elapsed * self._rate)
        bucket.updated = now
        if bucket.tokens >= 1.0:
            bucket.tokens -= 1.0
            return True
        return False
