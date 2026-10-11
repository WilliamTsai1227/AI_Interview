"""/me：帳號、偏好、用量、上手狀態、未結束的面試；刪除紀錄與帳號。"""
import datetime as dt
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field

from app.core import db, storage
from app.core.errors import AppError
from app.core.security import CurrentUser, current_user, verify_password
from app.modules.auth import repository as auth_repo
from app.modules.auth.service import user_out
from app.modules.interviews import service as interviews
from app.modules.reports import service as reports

router = APIRouter(prefix="/me", tags=["account"])
PLAN_LIMIT = interviews.PLAN_LIMIT
DEFAULT_PREFS = {"default_length": "standard", "default_persona": "warm", "default_language": "zh",
                 "desired_roles": [], "desired_locations": []}


class Prefs(BaseModel):
    default_length: Literal["quick", "standard", "deep"] | None = None
    default_persona: Literal["warm", "real", "tough"] | None = None
    default_language: Literal["zh", "en", "mixed"] | None = None
    desired_roles: list[str] | None = Field(default=None, max_length=10)
    desired_locations: list[str] | None = Field(default=None, max_length=10)


class MePatch(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=60)
    preferences: Prefs | None = None


class Confirm(BaseModel):
    confirm: str | None = None
    password: str | None = None


async def me_out(user_id: UUID) -> dict:
    async with db.connection() as conn:
        u = await conn.fetchrow("SELECT * FROM users WHERE id = $1", user_id)
        if not u:
            raise AppError(401, "UNAUTHENTICATED", "請重新登入")
        used = await conn.fetchval(
            "SELECT count(*) FROM interview_sessions WHERE user_id = $1 AND created_at >= date_trunc('month', now())", user_id)
        pr = await conn.fetchrow(
            "SELECT id, filename, parse_status FROM resumes WHERE user_id = $1 AND is_primary AND deleted_at IS NULL", user_id)
        has_target = await conn.fetchval("SELECT EXISTS (SELECT 1 FROM target_jobs WHERE user_id = $1 AND status = 'active')", user_id)
        has_done = await conn.fetchval("SELECT EXISTS (SELECT 1 FROM interview_reports WHERE user_id = $1)", user_id)
        active = await interviews.active_summary(conn, user_id)
    prefs = {**DEFAULT_PREFS, **(u["preferences"] or {})}
    async with db.connection() as conn:
        providers = [r["provider"] for r in await conn.fetch("SELECT provider FROM oauth_identities WHERE user_id = $1", user_id)]
    return {"user": {**user_out(u), "has_password": u["password_hash"] is not None, "providers": providers},
            "preferences": prefs,
            "usage": {"period": dt.date.today().strftime("%Y-%m"), "interviews_used": used,
                      "interviews_limit": PLAN_LIMIT.get(u["plan"], 10)},
            "onboarding": {"has_resume": pr is not None, "has_target": has_target, "has_completed_interview": has_done},
            "primary_resume": {"id": str(pr["id"]), "filename": pr["filename"], "parse_status": pr["parse_status"]} if pr else None,
            "active_interview": active}


@router.get("")
async def get_me(user: CurrentUser = Depends(current_user)):
    return await me_out(user.id)


@router.patch("")
async def patch_me(body: MePatch, user: CurrentUser = Depends(current_user)):
    async with db.transaction() as conn:
        if body.display_name:
            await conn.execute("UPDATE users SET display_name = $2 WHERE id = $1", user.id, body.display_name.strip())
        if body.preferences:
            cur = await conn.fetchval("SELECT preferences FROM users WHERE id = $1 FOR UPDATE", user.id) or {}
            cur.update(body.preferences.model_dump(exclude_none=True))
            await conn.execute("UPDATE users SET preferences = $2 WHERE id = $1", user.id, cur)
    return await me_out(user.id)


@router.delete("/interviews", status_code=202)
async def delete_interviews(body: Confirm, user: CurrentUser = Depends(current_user)):
    if body.confirm != "DELETE":
        raise AppError(400, "VALIDATION_ERROR", "請確認要刪除")
    n = await reports.delete_all(user.id)
    return {"deleted": n}


@router.delete("", status_code=202)
async def delete_me(body: Confirm, user: CurrentUser = Depends(current_user)):
    async with db.connection() as conn:
        u = await conn.fetchrow("SELECT * FROM users WHERE id = $1", user.id)
    if u["password_hash"]:
        if not verify_password(u["password_hash"], body.password or ""):
            raise AppError(400, "VALIDATION_ERROR", "密碼不正確", {"fields": {"password": "密碼不正確"}})
    elif body.confirm != "DELETE":
        raise AppError(400, "VALIDATION_ERROR", "請確認要刪除")
    async with db.connection() as conn:
        keys = [r["k"] for r in await conn.fetch(
            """SELECT a.audio_key AS k FROM answer_attempts a JOIN interview_sessions s ON s.id = a.session_id
                WHERE s.user_id = $1 AND a.audio_key IS NOT NULL
               UNION ALL SELECT file_key FROM resumes WHERE user_id = $1
               UNION ALL SELECT object_key FROM upload_tickets WHERE user_id = $1""", user.id)]
    async with db.transaction() as conn:
        await auth_repo.revoke_all_for_user(conn, user.id)
        await conn.execute("DELETE FROM interview_sessions WHERE user_id = $1", user.id)
        await conn.execute("DELETE FROM users WHERE id = $1", user.id)
    for i in range(0, len(keys), 500):
        try:
            await storage.delete(*keys[i:i + 500])
        except Exception:
            pass
    resp = Response(status_code=202)
    resp.delete_cookie("refresh_token", path="/api/v1/auth")
    return resp
