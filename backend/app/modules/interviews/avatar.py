"""面試官的擬真人像（Simli，spec/features/mock-interview.md「面試畫面」）。

後端只負責用 API 金鑰換 session token 與 ICE 伺服器；影像是瀏覽器 ↔ Simli 直連（WebRTC），
聲音（Live 的 GPT-Live 音軌、依題目的 TTS）也是瀏覽器直接送過去對嘴。連不上時前端改用插畫人像，面試照常進行。
"""
from uuid import UUID

import structlog

from app.ai import client as ai
from app.ai.agents import interviewer as iva
from app.core import db
from app.core.config import settings
from app.core.errors import AppError, not_found, state_conflict
from app.modules.interviews import service

log = structlog.get_logger()
MAX_CONNECTS = 20           # 每場最多換幾次 token（重新整理、斷線重連都算），避免有人拿來無限開 Simli 連線
SCRIPTED_MAX_SEC = 3600
LIVE_EXTRA_SEC = 300


async def connect(user_id: UUID, sid: UUID) -> dict:
    if not settings.simli_enabled:
        raise AppError(503, "AVATAR_UNAVAILABLE", "沒有設定擬真人像")
    async with db.connection() as conn:
        s = await conn.fetchrow("SELECT * FROM interview_sessions WHERE id = $1 AND user_id = $2", sid, user_id)
        if not s:
            raise not_found("找不到這場面試")
        if s["status"] not in ("ready", "in_progress"):
            raise state_conflict(s["state_version"], "面試不在進行中")
        used = await conn.fetchval(
            "SELECT count(*) FROM voice_events WHERE session_id = $1 AND event_type = 'avatar_connected'", sid)
    if used >= MAX_CONNECTS:
        raise AppError(429, "RATE_LIMITED", "這場面試的人像連線次數太多了，先用插畫人像繼續")
    if s["voice_mode"] == "live":     # 只開到這場對談的時間上限（Simli 依分鐘計費）
        max_sec = iva.LIVE_MINUTES[s["length_mode"]] * 60 + LIVE_EXTRA_SEC - (service._elapsed(s) if s["started_at"] else 0)
    else:
        max_sec = SCRIPTED_MAX_SEC
    try:
        r = await ai.simli_session(max_session_sec=max(120, max_sec), user_id=user_id, session_id=sid)
    except ai.QuotaExceeded:
        raise
    except Exception as e:
        log.warning("avatar_connect_failed", session_id=str(sid), error=str(e))
        raise AppError(503, "AVATAR_UNAVAILABLE", "暫時連不上擬真人像")
    async with db.connection() as conn:
        await conn.execute("INSERT INTO voice_events (session_id, source, event_type, payload) VALUES ($1, 'server', 'avatar_connected', $2)",
                           sid, {"provider": "simli", "max_session_sec": max(120, max_sec)})
    return {"provider": "simli", **r}
