"""PostgreSQL access. Canonical state lives here; Redis is never the system of record."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Connection, Engine, create_engine, text


def make_engine(url: str) -> Engine:
    return create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5, future=True)


@contextmanager
def tx(engine: Engine) -> Iterator[Connection]:
    """One transaction per unit of work; commits on success, rolls back on any exception."""
    with engine.begin() as con:
        yield con


def one(con: Connection, sql: str, **params):
    row = con.execute(text(sql), params).mappings().first()
    return dict(row) if row else None


def many(con: Connection, sql: str, **params) -> list[dict]:
    return [dict(r) for r in con.execute(text(sql), params).mappings().all()]


def run(con: Connection, sql: str, **params) -> int:
    return con.execute(text(sql), params).rowcount
