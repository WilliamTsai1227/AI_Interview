"""Live 即時對談（voice_mode = live，architecture.md §6.10）。

- 準備：worker 讀職缺、履歷、題庫（種子），產生面試官簡報（live_brief）→ 場次 ready。
- 對談：前端呼叫 connect，後端組好指示並代理 SDP 交換給 GPT-Live；之後音訊與字幕都是瀏覽器 ↔ OpenAI 直連，
  後端不持有長連線。前端把字幕整理成一段一段的發言，用 turns 存檔（可重送）。
- 面試中：每段回答答完（Ava 又開口），排 live_feedback 用快速模型即時評分，給右側欄顯示（只是參考）。
- 結束：service._finish 排 segment_live → 逐字稿切成主題目＋追問，寫成一般的 session_questions／answer_attempts，
  之後沿用 evaluate_attempt → build_report。
"""
from uuid import UUID

import structlog

from app.ai import client as ai
from app.ai import text
from app.ai.agents import interviewer as iva
from app.ai.agents import questions as qa
from app.core import db
from app.core.config import settings
from app.core.errors import AppError, not_found, state_conflict
from app.modules.interviews import service
from app.workers.queue import enqueue, tx

log = structlog.get_logger()
HISTORY_TURNS = 30          # 暫停後重新連線時帶回的對話段數（GPT-Live input 上限 128 則／8192 tokens）
HISTORY_CHARS = 600
OVERTIME_SEC = 120          # 超過時間上限這麼久就不再讓它連線


# ---------- 準備 ----------
async def handle_prepare(payload: dict) -> None:
    sid = UUID(payload["session_id"])
    async with db.connection() as conn:
        s = await conn.fetchrow("SELECT * FROM interview_sessions WHERE id = $1", sid)
    if not s or s["status"] != "preparing" or "topics" in (s["live_brief"] or {}):
        return
    seeds = (s["live_brief"] or {}).get("seeds", [])
    job, resume = s["job_snapshot"] or {}, s["resume_snapshot"] or None
    try:
        brief = await iva.build_brief(job=job, resume=resume, seeds=seeds, length_mode=s["length_mode"],
                                      language=s["language"], user_id=s["user_id"], session_id=sid)
    except Exception as e:      # 不讓場次卡在準備中：直接用題庫當議程
        log.warning("live_brief_failed", session_id=str(sid), error=str(e))
        brief = iva.fallback_brief(job, resume, seeds, s["length_mode"])
    async with db.connection() as conn:
        await conn.execute(
            """UPDATE interview_sessions SET live_brief = $2, status = 'ready', state_version = state_version + 1
                WHERE id = $1 AND status = 'preparing'""", sid, {"seeds": seeds, **brief})


# ---------- 連線 ----------
def _history(turns) -> list[dict]:
    out = []
    for t in turns:
        body = t["text"][:HISTORY_CHARS]
        if t["role"] == "interviewer":
            out.append({"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": body}]})
        else:
            out.append({"type": "message", "role": "user", "content": [{"type": "input_text", "text": body}]})
    return out


def _developer(text: str) -> dict:
    return {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": text}]}


async def connect(user_id: UUID, sid: UUID, sdp: str) -> dict:
    async with db.connection() as conn:
        s = await conn.fetchrow("SELECT * FROM interview_sessions WHERE id = $1 AND user_id = $2", sid, user_id)
        if not s:
            raise not_found("找不到這場面試")
        if s["voice_mode"] != "live":
            raise AppError(409, "STATE_CONFLICT", "這場面試不是 Live 對談")
        if s["status"] != "in_progress":
            raise state_conflict(s["state_version"], "面試不在進行中")
        name = await conn.fetchval("SELECT display_name FROM users WHERE id = $1", user_id)
        turns = list(reversed(await conn.fetch(
            "SELECT role, text FROM session_turns WHERE session_id = $1 ORDER BY seq DESC LIMIT $2", sid, HISTORY_TURNS)))
    if not settings.ai_enabled:
        raise AppError(503, "LIVE_UNAVAILABLE", "Live 對談需要設定 OpenAI 金鑰；開發模式請改用「依題目」")
    limit = iva.LIVE_MINUTES[s["length_mode"]] * 60
    elapsed = service._elapsed(s)
    if elapsed > limit + OVERTIME_SEC:
        raise AppError(409, "LIVE_TIME_UP", "這場對談的時間已經用完了，請結束面試產生報告")
    brief = s["live_brief"] or {}
    cue = iva.resume_cue(s["language"], elapsed // 60, max(1, (limit - elapsed) // 60)) if turns else iva.start_cue(s["language"])
    session = {
        "model": settings.live_model,
        "instructions": iva.instructions(brief=brief, job=s["job_snapshot"] or {}, resume=s["resume_snapshot"] or None,
                                         persona=s["persona"], language=s["language"], length_mode=s["length_mode"],
                                         candidate=service._short(name)),
        "input": [*_history(turns), _developer(cue)],
    }
    if settings.live_voice:
        session["audio"] = {"output": {"voice": settings.live_voice}}
    if settings.live_delegate_model:
        session["delegation"] = {"type": "responses", "responses": {
            "model": settings.live_delegate_model, "instructions": iva.DELEGATE_INSTRUCTIONS,
            "tools": iva.TOOLS, "tool_choice": "auto"}}
    try:
        r = await ai.live_session(session, sdp, user_id=user_id, session_id=sid)
    except ai.QuotaExceeded:
        raise
    except Exception as e:
        log.warning("live_connect_failed", session_id=str(sid), error=str(e))
        raise AppError(503, "LIVE_UNAVAILABLE", "暫時連不上 Live 對談，請稍後再試")
    async with db.connection() as conn:
        await conn.execute("UPDATE interview_sessions SET realtime_call_id = $2, last_heartbeat_at = now() WHERE id = $1", sid, r["id"])
        await conn.execute("INSERT INTO voice_events (session_id, source, event_type, payload) VALUES ($1, 'server', 'live_connected', $2)",
                           sid, {"live_session_id": r["id"], "resumed": bool(turns)})
    return {"sdp": r["sdp"], "live_session_id": r["id"], "time_limit_sec": limit, "elapsed_sec": elapsed,
            "wrap_up_sec": settings.live_wrap_up_sec}


# ---------- 逐字稿 ----------
async def add_turns(user_id: UUID, sid: UUID, turns: list[dict], usage_seconds: float | None) -> None:
    """存對話段落（同一個 seq 重送不會重複）。暫停、結束後幾秒內補送的段落也收。"""
    async with db.connection() as conn:
        s = await conn.fetchrow("SELECT status, voice_mode, language FROM interview_sessions WHERE id = $1 AND user_id = $2", sid, user_id)
        if not s:
            raise not_found("找不到這場面試")
        if s["voice_mode"] != "live" or s["status"] not in ("in_progress", "paused"):
            raise AppError(409, "STATE_CONFLICT", "這場面試不能再加入對話")
        for t in turns:
            body = text.to_traditional((t.get("text") or "").strip(), s["language"])
            if body:
                await conn.execute(
                    """INSERT INTO session_turns (session_id, seq, role, text, started_ms, ended_ms) VALUES ($1,$2,$3,$4,$5,$6)
                       ON CONFLICT (session_id, seq) DO NOTHING""",
                    sid, t["seq"], t["role"], body[:8000], t.get("started_ms"), t.get("ended_ms"))
        await conn.execute("UPDATE interview_sessions SET last_heartbeat_at = now() WHERE id = $1 AND status = 'in_progress'", sid)
    if any((t.get("text") or "").strip() for t in turns):
        await _queue_feedback(sid)
    if usage_seconds:
        await ai.log_live_usage(usage_seconds, user_id=user_id, session_id=sid)


# ---------- 即時評分 ----------
async def _queue_feedback(sid: UUID) -> None:
    """新答完的段落各排一個評分工作（同一段只排一次）。"""
    async with tx() as conn:
        turns = [dict(t) for t in await conn.fetch(
            "SELECT seq, role, text FROM session_turns WHERE session_id = $1 ORDER BY seq", sid)]
        have = {r["seq"] for r in await conn.fetch("SELECT seq FROM live_feedback WHERE session_id = $1", sid)}
        for ex in iva.exchanges(turns):
            if ex["seq"] in have:
                continue
            ok = await conn.fetchval(
                """INSERT INTO live_feedback (session_id, seq, answer_seqs, question, answer) VALUES ($1,$2,$3,$4,$5)
                   ON CONFLICT (session_id, seq) DO NOTHING RETURNING seq""",
                sid, ex["seq"], ex["answer_seqs"], ex["question"], ex["answer"])
            if ok is not None:
                await enqueue(conn, "live_feedback", {"session_id": str(sid), "seq": ex["seq"]}, queue="interactive",
                              dedupe_key=f"live_feedback:{sid}:{ex['seq']}")


async def handle_feedback(payload: dict) -> None:
    sid, seq = UUID(payload["session_id"]), int(payload["seq"])
    async with db.connection() as conn:
        f = await conn.fetchrow(
            """SELECT f.*, s.user_id, s.language, s.job_snapshot FROM live_feedback f
                 JOIN interview_sessions s ON s.id = f.session_id WHERE f.session_id = $1 AND f.seq = $2""", sid, seq)
    if not f or f["status"] != "pending":
        return
    try:
        out = await iva.quick_feedback(question=f["question"], answer=f["answer"], job=f["job_snapshot"] or {},
                                       language=f["language"], user_id=f["user_id"], session_id=sid)
    except Exception as e:      # 只是面試中的參考，失敗就標記，不重試（正式評分在結束後）
        log.warning("live_feedback_failed", session_id=str(sid), seq=seq, error=str(e))
        async with db.connection() as conn:
            await conn.execute("UPDATE live_feedback SET status = 'failed', finished_at = now() WHERE session_id = $1 AND seq = $2", sid, seq)
        return
    async with db.connection() as conn:
        await conn.execute(
            """UPDATE live_feedback SET status = $3, title = $4, score = $5, strengths = $6, improvements = $7, tip = $8,
                      finished_at = now() WHERE session_id = $1 AND seq = $2""",
            sid, seq, "done" if out.is_question else "skipped", out.title[:60], out.score, out.strengths[:2],
            out.improvements[:2], out.tip[:80])


# ---------- 結束：切題 ----------
async def handle_segment(payload: dict) -> None:
    sid = UUID(payload["session_id"])
    async with db.connection() as conn:
        s = await conn.fetchrow("SELECT * FROM interview_sessions WHERE id = $1", sid)
        if not s or s["status"] != "completed":
            return
        done = await conn.fetchval("SELECT count(*) FROM session_questions WHERE session_id = $1", sid)
        turns = [dict(t) for t in await conn.fetch(
            "SELECT seq, role, text, started_ms, ended_ms FROM session_turns WHERE session_id = $1 ORDER BY seq", sid)]
    if done:        # 工作重跑：已經切過，評分各自會排報告
        return
    topics = (s["live_brief"] or {}).get("topics", [])
    try:
        groups = await iva.segment(turns, topics=topics, language=s["language"], user_id=s["user_id"], session_id=sid)
    except ai.QuotaExceeded:
        raise
    except Exception as e:      # 切題失敗也要出報告：退回「面試官一段＋候選人一段＝一題」
        log.warning("live_segment_failed", session_id=str(sid), error=str(e))
        groups = iva.fallback_segment(turns, topics)
    by_topic = {t["id"]: t for t in topics}
    stt = settings.live_model if settings.ai_enabled else "fake"
    async with tx() as conn:
        await conn.fetchrow("SELECT id FROM interview_sessions WHERE id = $1 FOR UPDATE", sid)
        if await conn.fetchval("SELECT count(*) FROM session_questions WHERE session_id = $1", sid):
            return
        for i, g in enumerate(groups):
            tp = by_topic.get(g["topic_id"]) or {}
            rubric = tp.get("rubric") or qa._default_rubric(tp.get("expected_points") or ["具體情境", "你的做法", "結果與反思"])
            main = await conn.fetchrow(
                """INSERT INTO session_questions (session_id, source_item_id, kind, order_no, followup_no, followup_mode,
                          followup_target, category, difficulty, text, competency, expected_points, rubric, tts_status, status, asked_at)
                   VALUES ($1,$2,'main',$3,0,$4,$5,$6,$7,$8,$9,$10,$11,'not_needed','answered', now()) RETURNING id""",
                sid, UUID(tp["source_item_id"]) if tp.get("source_item_id") else None, i + 1,
                "adaptive" if g["followups"] else "none", len(g["followups"]), g["category"], tp.get("difficulty") or "medium",
                g["main"]["question"], tp.get("competency") or g["category"], tp.get("expected_points") or [], rubric)
            await _attempt(conn, s, main["id"], g["main"], stt)
            for j, f in enumerate(g["followups"]):
                fq = await conn.fetchrow(
                    """INSERT INTO session_questions (session_id, parent_id, kind, order_no, followup_no, followup_source,
                              category, difficulty, text, rubric, tts_status, status, asked_at)
                       VALUES ($1,$2,'followup',$3,$4,'live',$5,$6,$7,$8,'not_needed','answered', now()) RETURNING id""",
                    sid, main["id"], i + 1, j + 1, g["category"], tp.get("difficulty") or "medium", f["question"], rubric)
                await _attempt(conn, s, fq["id"], f, stt)
        await conn.execute("UPDATE interview_sessions SET planned_question_count = $2 WHERE id = $1", sid, max(1, len(groups)))
        if not groups:      # 候選人有說話但切不出題目（例如只有寒暄）：直接產生「沒有內容」的報告
            await enqueue(conn, "build_report", {"session_id": str(sid)}, dedupe_key=f"build_report:{sid}")


async def _attempt(conn: db.Connection, s, qid: UUID, ex: dict, stt: str) -> None:
    a = await conn.fetchrow(
        """INSERT INTO answer_attempts (session_id, session_question_id, attempt_no, is_final, status, answer_started_at,
                  answer_ended_at, audio_duration_ms, quick_transcript, final_transcript, transcript_status, stt_model)
           VALUES ($1,$2,1,true,'saved',$3,$4,$5,$6,$6,'done',$7) RETURNING id""",
        s["id"], qid, s["started_at"] or s["created_at"], s["ended_at"], ex["duration_ms"], ex["answer"], stt)
    await conn.execute("INSERT INTO evaluations (attempt_id, session_id, session_question_id) VALUES ($1, $2, $3)",
                       a["id"], s["id"], qid)
    await enqueue(conn, "evaluate_attempt", {"attempt_id": str(a["id"])}, dedupe_key=f"evaluate:{a['id']}")
