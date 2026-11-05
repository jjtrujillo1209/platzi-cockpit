"""Acceso de solo-lectura/escritura ligera a mundo_orquestador.db (SQLite).

El cockpit escribe únicamente en webhook_events (vía el receptor de SendGrid);
todo lo demás es lectura.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from config import TRACKING_DB


@contextmanager
def connect(db_path: Path = TRACKING_DB) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def db_available() -> bool:
    return TRACKING_DB.exists()


def fetch_all(query: str, params: tuple = ()) -> list[dict]:
    if not db_available():
        return []
    try:
        with connect() as conn:
            return [dict(r) for r in conn.execute(query, params).fetchall()]
    except sqlite3.Error:
        return []


def table_exists(name: str) -> bool:
    rows = fetch_all(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
    )
    return bool(rows)
