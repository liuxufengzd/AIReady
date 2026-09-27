"""PostgreSQL client shared by services that read and write the same tables.

The pool is synchronous and opened on first use.
"""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from functools import cached_property
from typing import Any

import psycopg
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

_MIN_POOL_SIZE = 1
_MAX_POOL_SIZE = 10


def _env_str(name: str, default: str) -> str:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return int(raw)


class DBClient:
    """Shared PostgreSQL connection pool.

    Unset fields are read from ``DB_HOST``, ``DB_PORT``, ``DB_USER``,
    and ``DB_NAME`` when the pool opens. ``connection`` checks out a
    connection; ``execute``, ``fetchone``, and ``fetchall`` run a single
    statement.
    """

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        user: str | None = None,
        dbname: str | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.user = user
        self.dbname = dbname

    @cached_property
    def pool(self) -> ConnectionPool:
        """Open the pool on first use and keep it for the process."""
        pool = ConnectionPool(
            conninfo=make_conninfo(
                host=self.host or _env_str("DB_HOST", "localhost"),
                port=self.port or _env_int("DB_PORT", 5432),
                user=self.user or _env_str("DB_USER", "postgres"),
                password=_env_str("DB_PASSWORD", "postgres"),
                dbname=self.dbname or _env_str("DB_NAME", "ai_ready"),
            ),
            min_size=_MIN_POOL_SIZE,
            max_size=_MAX_POOL_SIZE,
            open=False,
            kwargs={"autocommit": True},
        )
        pool.open(wait=True)
        return pool

    @contextmanager
    def connection(self) -> Iterator[psycopg.Connection]:
        with self.pool.connection() as conn:
            yield conn

    def execute(self, query: str, params: tuple[Any, ...] | None = None) -> None:
        with self.connection() as conn:
            conn.execute(query, params)

    def execute_rowcount(
        self, query: str, params: tuple[Any, ...] | None = None
    ) -> int:
        """Run one statement and return how many rows it affected."""
        with self.connection() as conn:
            cursor = conn.execute(query, params)
            return cursor.rowcount

    def fetchone(
        self, query: str, params: tuple[Any, ...] | None = None
    ) -> dict[str, Any] | None:
        with self.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cursor:
                cursor.execute(query, params)
                return cursor.fetchone()

    def fetchall(
        self, query: str, params: tuple[Any, ...] | None = None
    ) -> list[dict[str, Any]]:
        with self.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cursor:
                cursor.execute(query, params)
                return cursor.fetchall()

    def close(self) -> None:
        """Close the pool if it was opened. Use __dict__ because cached_property is used."""
        pool = self.__dict__.get("pool")
        if isinstance(pool, ConnectionPool):
            pool.close()
            del self.__dict__["pool"]
