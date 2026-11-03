"""The storage interface the domain modules depend on (SQLite and PostgreSQL implement it)."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import AbstractContextManager
from typing import Any, Protocol

Row = Any  # a mapping-like row: ``row["column"]`` works on both back ends
Params = Sequence[Any]


class Db(Protocol):
    """Parameters are always ``?`` placeholders; SQL is never built from input."""

    def transaction(self) -> AbstractContextManager[Any]: ...

    def query(self, sql: str, params: Params = ()) -> list[Row]: ...

    def execute(self, sql: str, params: Params = ()) -> int: ...

    def close(self) -> None: ...


__all__ = ["Db", "Iterator", "Params", "Row"]
