"""開發用種子資料：示範帳號、管理員。

不再建立範例平台職缺：職缺只保留使用者自己貼上的。

用法：python -m app.seed [--admin email]
"""
import asyncio
import sys

from app.core import db
from app.core.security import hash_password


async def seed(admin_email: str | None) -> None:
    await db.init_pool()
    async with db.transaction() as conn:
        await conn.execute(
            """INSERT INTO users (email, password_hash, display_name) VALUES ('demo@example.com', $1, '陳品妤')
               ON CONFLICT (email) DO NOTHING""", hash_password("demo1234"))
        if admin_email:
            await conn.execute("UPDATE users SET role = 'admin' WHERE email = $1", admin_email)
    await db.close_pool()
    print("seeded demo user demo@example.com / demo1234"
          + (f"; admin = {admin_email}" if admin_email else ""))


if __name__ == "__main__":
    args = sys.argv[1:]
    asyncio.run(seed(args[args.index("--admin") + 1] if "--admin" in args else None))
