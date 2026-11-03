"""Injectable clock so time-dependent logic is deterministic in tests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    """Anything that can say what time it is (timezone-aware, UTC)."""

    def now(self) -> datetime: ...


class SystemClock:
    """Real time. Used in production."""

    def now(self) -> datetime:
        return datetime.now(UTC)


@dataclass
class FixedClock:
    """A clock that only moves when told to. Used in tests."""

    current: datetime

    def now(self) -> datetime:
        return self.current

    def advance(self, **kwargs: float) -> None:
        self.current = self.current + timedelta(**kwargs)
