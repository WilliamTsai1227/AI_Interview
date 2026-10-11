"""asyncpg 連線池與交易 helper。所有查詢都是手寫參數化 SQL（見 architecture.md §4.2.1）。"""
import json
from contextlib import asynccontextmanager
from typing import AsyncIterator

import asyncpg
from pgvector.asyncpg import register_vector

from app.core.config import settings

_pool: asyncpg.Pool | None = None

Connection = asyncpg.Connection
Record = asyncpg.Record


async def _init_conn(conn: asyncpg.Connection) -> None:
    for typ in ("jsonb", "json"):
        await conn.set_type_codec(typ, encoder=lambda v: json.dumps(v, ensure_ascii=False),
                                  decoder=json.loads, schema="pg_catalog")
    try:
        await register_vector(conn)
    except ValueError:
        pass  # vector 擴充尚未建立（第一次遷移前）


async def init_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(settings.database_url, min_size=settings.db_pool_min,
                                          max_size=settings.db_pool_max, init=_init_conn)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def pool() -> asyncpg.Pool:
    assert _pool is not None, "DB pool not initialised"
    return _pool


@asynccontextmanager
async def connection() -> AsyncIterator[asyncpg.Connection]:
    async with pool().acquire() as conn:
        yield conn


@asynccontextmanager
async def transaction() -> AsyncIterator[asyncpg.Connection]:
    async with pool().acquire() as conn:
        async with conn.transaction():
            yield conn


def row(r: asyncpg.Record | None) -> dict | None:
    return dict(r) if r is not None else None


def rows(rs: list[asyncpg.Record]) -> list[dict]:
    return [dict(r) for r in rs]
