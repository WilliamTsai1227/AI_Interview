"""追問語音串流：GET /api/v1/tts/q/{qid}?exp=&sig=（短效簽章，給 <audio> 直接使用）。"""
from uuid import UUID

from fastapi import APIRouter
from fastapi.responses import RedirectResponse, StreamingResponse

from app.core import db
from app.core.errors import AppError, not_found
from app.modules.voice import service as voice

router = APIRouter(prefix="/tts", tags=["voice"])


@router.get("/q/{qid}")
async def question_audio(qid: UUID, exp: int, sig: str):
    if not voice.verify(qid, exp, sig):
        raise AppError(403, "FORBIDDEN", "語音連結已失效")
    async with db.connection() as conn:
        q = await conn.fetchrow(
            """SELECT q.id, q.text, q.tts_audio_key, s.language, s.persona, s.user_id, s.id AS session_id
                 FROM session_questions q JOIN interview_sessions s ON s.id = q.session_id WHERE q.id = $1""", qid)
    if not q:
        raise not_found()
    key = q["tts_audio_key"] or await voice.cached_key(q["text"], q["language"], q["persona"])
    if key:                                   # 已經合成過（重播、或另一個請求已完成）：直接給檔案
        return RedirectResponse(voice.url(key), status_code=302)
    return StreamingResponse(voice.stream_question(q, q["language"], q["persona"], q["user_id"], q["session_id"]),
                             media_type="audio/mpeg", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
