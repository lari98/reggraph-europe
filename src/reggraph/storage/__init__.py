"""Persistence (SQLite in Phase 1; PostgreSQL behind the same interface in Phase 4)."""

from reggraph.storage.sqlite import Database

__all__ = ["Database"]
