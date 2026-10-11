"""極簡遷移：依檔名順序執行 migrations/*.sql，每個檔案一個交易，記錄在 schema_migrations。

用法：python -m app.core.migrate
"""
import asyncio
import calendar
import datetime as dt
from pathlib import Path

import asyncpg

from app.core.config import settings

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"
PARTITIONED = ("llm_calls", "voice_events", "app_events")


async def ensure_partitions(conn: asyncpg.Connection, months_ahead: int = 2) -> None:
    """建立本月與之後幾個月的 log 分區（worker 每日也會呼叫）。"""
    today = dt.date.today().replace(day=1)
    for i in range(months_ahead + 1):
        y, m = divmod(today.month - 1 + i, 12)
        start = dt.date(today.year + y, m + 1, 1)
        end = start + dt.timedelta(days=calendar.monthrange(start.year, start.month)[1])
        for t in PARTITIONED:
            exists = await conn.fetchval("SELECT to_regclass($1)", f"public.{t}")
            if not exists:
                continue
            name = f"{t}_{start:%Y_%m}"
            await conn.execute(
                f"CREATE TABLE IF NOT EXISTS {name} PARTITION OF {t} "
                f"FOR VALUES FROM ('{start}') TO ('{end}')"
            )


async def migrate(dsn: str | None = None) -> list[str]:
    conn = await asyncpg.connect(dsn or settings.database_url)
    applied: list[str] = []
    try:
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
        )
        done = {r["name"] for r in await conn.fetch("SELECT name FROM schema_migrations")}
        for f in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if f.name in done:
                continue
            async with conn.transaction():
                await conn.execute(f.read_text(encoding="utf-8"))
                await conn.execute("INSERT INTO schema_migrations (name) VALUES ($1)", f.name)
            applied.append(f.name)
        await ensure_partitions(conn)
    finally:
        await conn.close()
    return applied


if __name__ == "__main__":
    names = asyncio.run(migrate())
    print("applied:", ", ".join(names) if names else "(none)")
