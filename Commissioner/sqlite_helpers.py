"""
Commissioner/sqlite_helpers.py -- SQLite helpers and custom collations.

Single home for SQLite utility functions and custom collations (e.g. RootsMagic's
RMNOCASE collation) used across Antiquarian modules.
"""

from __future__ import annotations

import sqlite3
from typing import Optional

__all__ = [
    "rmnocase",
    "register_rmnocase",
]


def rmnocase(s1: Optional[str], s2: Optional[str]) -> int:
    """Collation function for RootsMagic's custom RMNOCASE (case-insensitive sort)."""
    s1_clean = s1.lower() if s1 else ""
    s2_clean = s2.lower() if s2 else ""
    if s1_clean == s2_clean:
        return 0
    return -1 if s1_clean < s2_clean else 1


def register_rmnocase(conn: sqlite3.Connection) -> sqlite3.Connection:
    """Registers the RMNOCASE collation on an open SQLite connection."""
    conn.create_collation("RMNOCASE", rmnocase)
    return conn
