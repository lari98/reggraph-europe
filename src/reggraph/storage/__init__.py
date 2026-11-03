"""Persistence: SQLite (default) and PostgreSQL behind one interface (``Db``)."""

from __future__ import annotations

from reggraph.storage.base import Db, Row
from reggraph.storage.sqlite import Database


def open_database(path: str = ":memory:", url: str = "", schema: str = "public") -> Db:
    """PostgreSQL when ``url`` is given, otherwise SQLite at ``path``."""
    if url:
        from reggraph.storage.postgres import PostgresDatabase

        return PostgresDatabase(url, schema)
    return Database(path)


__all__ = ["Database", "Db", "Row", "open_database"]
