from uuid import UUID

from app.core.db import Connection, Record


async def get_user_by_email(conn: Connection, email: str) -> Record | None:
    return await conn.fetchrow("SELECT * FROM users WHERE email = $1 AND status <> 'deleted'", email)


async def get_user(conn: Connection, user_id: UUID) -> Record | None:
    return await conn.fetchrow("SELECT * FROM users WHERE id = $1 AND status = 'active'", user_id)


async def insert_user(conn: Connection, email: str, password_hash: str, display_name: str) -> Record:
    return await conn.fetchrow(
        """
        INSERT INTO users (email, password_hash, display_name, last_login_at)
        VALUES ($1, $2, $3, now())
        RETURNING *
        """, email, password_hash, display_name)


async def touch_login(conn: Connection, user_id: UUID) -> None:
    await conn.execute("UPDATE users SET last_login_at = now() WHERE id = $1", user_id)


async def insert_refresh(conn: Connection, user_id: UUID, family_id: UUID, token_hash: str, days: int,
                         user_agent: str | None, ip: str | None) -> None:
    await conn.execute(
        """
        INSERT INTO refresh_tokens (user_id, family_id, token_hash, expires_at, user_agent, ip)
        VALUES ($1, $2, $3, now() + make_interval(days => $4), $5, $6::inet)
        """, user_id, family_id, token_hash, days, (user_agent or "")[:300], ip)


async def get_refresh_for_update(conn: Connection, token_hash: str) -> Record | None:
    return await conn.fetchrow("SELECT * FROM refresh_tokens WHERE token_hash = $1 FOR UPDATE", token_hash)


async def revoke_refresh(conn: Connection, token_id: UUID) -> None:
    await conn.execute("UPDATE refresh_tokens SET revoked_at = now() WHERE id = $1 AND revoked_at IS NULL", token_id)


async def revoke_family(conn: Connection, family_id: UUID) -> None:
    await conn.execute("UPDATE refresh_tokens SET revoked_at = now() WHERE family_id = $1 AND revoked_at IS NULL",
                       family_id)


async def revoke_all_for_user(conn: Connection, user_id: UUID) -> None:
    await conn.execute("UPDATE refresh_tokens SET revoked_at = now() WHERE user_id = $1 AND revoked_at IS NULL",
                       user_id)
