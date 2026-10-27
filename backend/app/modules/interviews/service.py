"""面試狀態機（architecture.md §6、api.md §10）。interviews 是唯一可以改變面試狀態的模組。

規則：
- 所有推進都在 `SELECT … FOR UPDATE` 的交易中，並檢查 expected_version（樂觀鎖）。
- 題目順序由系統決定；追問次數由程式計數（count(*) WHERE parent_id = 主題目），AI 只寫內容。
- 回答錄音上傳成功且寫入成功，才會前進。
"""
import asyncio
import datetime as dt
import json
from uuid import UUID

import asyncpg
import structlog

from app.ai import client as ai
from app.ai import limiter
from app.ai.agents import followup as fua
from app.ai.agents import questions as qa
from app.core import db, redis
from app.core.config import settings
from app.core.errors import AppError, bad_request, not_found, state_conflict
from app.modules.questions import service as questions
from app.modules.resumes import service as resumes
from app.modules.uploads import service as uploads
from app.modules.voice import service as voice
from app.workers.queue import enqueue, tx

log = structlog.get_logger()
LENGTH = {"quick": 3, "standard": 5, "deep": 8}
ACTIVE = ("preparing", "ready", "in_progress", "paused")
PLAN_LIMIT = {"free": 10, "pro": 30}
RESUME_WINDOW = dt.timedelta(hours=24)


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


# ---------- 讀取與輸出 ----------
async def _lock(conn: db.Connection, user_id: UUID, sid: UUID) -> db.Record:
    s = await conn.fetchrow("SELECT * FROM interview_sessions WHERE id = $1 AND user_id = $2 FOR UPDATE", sid, user_id)
    if not s:
        raise not_found("找不到這場面試")
    return s


def _check(s, expected: int | None, *, status: tuple[str, ...] | None = None, phase: tuple[str, ...] | None = None):
    if expected is not None and expected != s["state_version"]:
        raise state_conflict(s["state_version"])
    if status and s["status"] not in status:
        raise state_conflict(s["state_version"], "面試狀態已變更")
    if phase and s["phase"] not in phase:
        raise state_conflict(s["state_version"], "目前不能執行這個動作")


async def _bump(conn: db.Connection, sid: UUID, **fields) -> db.Record:
    sets, args = ["state_version = state_version + 1"], [sid]
    for k, v in fields.items():
        args.append(v)
        sets.append(f"{k} = ${len(args)}")
    return await conn.fetchrow(f"UPDATE interview_sessions SET {', '.join(sets)} WHERE id = $1 RETURNING *", *args)


def _elapsed(s) -> int:
    if not s["started_at"]:
        return 0
    end = s["ended_at"] or s["paused_at"] or _now()
    return max(0, int((end - s["started_at"]).total_seconds()) - (s["paused_total_sec"] or 0))


def _tts_url(q) -> str | None:
    if q["tts_audio_key"]:
        return voice.url(q["tts_audio_key"])
    if q["tts_status"] == "pending" and q["kind"] == "followup" and settings.ai_enabled:
        return voice.stream_url(q["id"])          # 追問：邊合成邊播放
    return None                                    # 開發模式或合成失敗：前端用瀏覽器語音


async def _question_out(conn: db.Connection, q, language: str, *, with_tts: bool = True) -> dict:
    main = q if q["kind"] == "main" else await conn.fetchrow("SELECT * FROM session_questions WHERE id = $1", q["parent_id"])
    out = {"id": str(q["id"]), "kind": q["kind"], "order_no": q["order_no"], "followup_no": q["followup_no"],
           "followup_mode": main["followup_mode"], "followup_target": main["followup_target"],
           "followup_source": q["followup_source"], "category": q["category"], "text": q["text"],
           "tts_status": q["tts_status"], "tts_url": _tts_url(q) if with_tts else None,
           "tts_expires_in": 900, "delivery": "tts", "realtime_instructions": None}
    return out


async def session_out(conn: db.Connection, s) -> dict:
    progress = await conn.fetchrow(
        """
        SELECT count(*) FILTER (WHERE kind = 'main' AND status = 'answered') AS answered,
               count(*) FILTER (WHERE kind = 'main' AND status = 'skipped') AS skipped,
               count(*) FILTER (WHERE kind = 'main') AS total_main
          FROM session_questions WHERE session_id = $1
        """, s["id"])
    cur = await conn.fetchrow("SELECT * FROM session_questions WHERE id = $1", s["current_question_id"]) if s["current_question_id"] else None
    att = await conn.fetchrow("SELECT * FROM answer_attempts WHERE id = $1", s["current_attempt_id"]) if s["current_attempt_id"] else None
    cq = await _question_out(conn, cur, s["language"]) if cur else None
    total = progress["total_main"] or s["planned_question_count"]
    job = s["job_snapshot"] or {}
    return {
        "id": str(s["id"]), "status": s["status"], "phase": s["phase"], "state_version": s["state_version"],
        "voice_mode": s["voice_mode"], "length_mode": s["length_mode"], "persona": s["persona"], "language": s["language"],
        "target_id": str(s["target_job_id"]),
        "job": {"id": str(s["job_post_id"]), "company_name": job.get("company_name"), "title": job.get("title")},
        "progress": {"current_main_no": cq["order_no"] if cq else (total if s["status"] == "completed" else 1),
                     "total_main": total, "answered": progress["answered"], "skipped": progress["skipped"],
                     "followup_no": cq["followup_no"] if cq else 0,
                     "followup_total": (cq["followup_target"] or 0) if cq and cq["followup_mode"] != "none" else 0},
        "current_question": cq,
        "current_attempt": {"id": str(att["id"]), "attempt_no": att["attempt_no"], "status": att["status"],
                            "answer_started_at": att["answer_started_at"]} if att else None,
        "cues": await voice.cues(s["language"]),
        "limits": {"answer_max_sec": settings.answer_max_sec, "silence_prompt_sec": settings.silence_prompt_sec,
                   "heartbeat_sec": settings.heartbeat_sec},
        "started_at": s["started_at"], "elapsed_sec": _elapsed(s), "paused_at": s["paused_at"],
        "resume_deadline": s["paused_at"] + RESUME_WINDOW if s["paused_at"] else None,
        "ended_at": s["ended_at"], "end_reason": s["end_reason"],
    }


async def _active(conn: db.Connection, user_id: UUID) -> db.Record | None:
    return await conn.fetchrow(
        "SELECT * FROM interview_sessions WHERE user_id = $1 AND status = ANY($2::text[]) ORDER BY created_at DESC LIMIT 1",
        user_id, list(ACTIVE))


async def get_active(user_id: UUID) -> dict | None:
    async with db.connection() as conn:
        s = await _active(conn, user_id)
        return await session_out(conn, s) if s else None


async def active_summary(conn: db.Connection, user_id: UUID) -> dict | None:
    s = await _active(conn, user_id)
    if not s:
        return None
    o = await session_out(conn, s)
    return {"id": o["id"], "status": o["status"], "target_id": o["target_id"], "company_name": o["job"]["company_name"],
            "answered": o["progress"]["answered"], "current_main_no": o["progress"]["current_main_no"],
            "total_main": o["progress"]["total_main"], "resume_deadline": o["resume_deadline"]}


async def get(user_id: UUID, sid: UUID) -> dict:
    async with tx() as conn:
        s = await _lock(conn, user_id, sid)
        # 錄音中太久（例如分頁當掉）：作答標記 interrupted，退回「輪到你了」
        if s["phase"] == "answering" and s["current_attempt_id"]:
            a = await conn.fetchrow("SELECT * FROM answer_attempts WHERE id = $1", s["current_attempt_id"])
            if a and (_now() - a["answer_started_at"]).total_seconds() > settings.answer_max_sec + 30:
                await conn.execute("UPDATE answer_attempts SET status = 'interrupted' WHERE id = $1", a["id"])
                s = await _bump(conn, sid, phase="awaiting_answer", current_attempt_id=None)
        return await session_out(conn, s)


# ---------- 建立場次 ----------
async def create(user_id: UUID, target_id: UUID, resume_id: UUID | None, resume_given: bool, length_mode: str,
                 persona: str, language: str, consent: bool) -> dict:
    if not consent:
        raise bad_request("開始面試前需要同意錄音", {"recording_consent": "required"})
    await redis.rate_limit("interview_create", str(user_id))
    async with db.connection() as conn:
        act = await _active(conn, user_id)
        if act:
            raise AppError(409, "ACTIVE_SESSION_EXISTS", "你有一場還沒結束的面試", {"session_id": str(act["id"])})
        if await limiter.live_interviews() >= settings.max_live_interviews:
            raise AppError(503, "SERVICE_BUSY", "目前同時面試的人比較多，請稍候再試", {"estimated_wait_sec": 30},
                           headers={"Retry-After": "30"})
        u = await conn.fetchrow("SELECT * FROM users WHERE id = $1", user_id)
        if not settings.is_dev:
            used = await conn.fetchval(
                "SELECT count(*) FROM interview_sessions WHERE user_id = $1 AND created_at >= date_trunc('month', now())", user_id)
            if used >= PLAN_LIMIT.get(u["plan"], 10):
                raise AppError(403, "PLAN_LIMIT_REACHED", "這個月的面試次數已經用完了",
                               {"used": used, "limit": PLAN_LIMIT.get(u["plan"], 10)})
        t = await conn.fetchrow(
            """SELECT t.*, qs.id AS set_id, qs.status AS set_status FROM target_jobs t
                 JOIN question_sets qs ON qs.target_job_id = t.id WHERE t.id = $1 AND t.user_id = $2 AND t.status = 'active'""",
            target_id, user_id)
        if not t:
            raise not_found("找不到這個目標職缺")
        if t["set_status"] != "ready":
            raise AppError(409, "RESOURCE_NOT_READY", "Ava 還在出題，完成後就能開始")
        n = LENGTH[length_mode]
        items = await conn.fetch(
            "SELECT * FROM question_set_items WHERE set_id = $1 AND deleted_at IS NULL ORDER BY order_no LIMIT $2", t["set_id"], n)
        job = await conn.fetchrow("SELECT * FROM job_posts WHERE id = $1", t["job_post_id"])
        rid = resume_id if resume_given else t["resume_id"]
        r = await resumes.owned(conn, user_id, rid) if rid else None
    job_snap = {"job_post_id": str(job["id"]), "company_name": job["company_name"], "title": job["title"],
                "about": job["about"] or job["summary"], "duties": job["duties"] or [], "requirements": job["requirements"] or [],
                "tags": list(job["tags"] or []), "description": (job["raw_text"] or "")[:4000]}
    resume_snap = resumes.snapshot(r)
    # 題組不夠：自動補題並寫回題組（不讓使用者卡在「題數不足」）
    fill: list[dict] = []
    if len(items) < n:
        fill = await qa.plan_questions(job=job_snap, resume=resume_snap, categories=[], difficulty="medium",
                                       count=n - len(items), note="", existing=[i["text"] for i in items],
                                       language="en" if language == "en" else "zh", user_id=user_id)
    try:
        async with tx() as conn:
            if fill:
                ids = await questions.append_items(conn, t["set_id"], fill, source="ai")
                items = list(items) + list(await conn.fetch(
                    "SELECT * FROM question_set_items WHERE id = ANY($1::uuid[]) ORDER BY order_no", ids))
            planned = {}
            if items:
                for f in await conn.fetch(
                        "SELECT * FROM question_set_followups WHERE item_id = ANY($1::uuid[]) ORDER BY order_no",
                        [i["id"] for i in items]):
                    planned.setdefault(f["item_id"], []).append({"id": str(f["id"]), "text": f["text"]})
            s = await conn.fetchrow(
                """
                INSERT INTO interview_sessions (user_id, target_job_id, job_post_id, resume_id, question_set_id, length_mode,
                       persona, language, voice_mode, job_snapshot, resume_snapshot, planned_question_count,
                       recording_consent_at, last_heartbeat_at)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,'scripted',$9,$10,$11, now(), now()) RETURNING *
                """, user_id, target_id, job["id"], r["id"] if r else None, t["set_id"], length_mode, persona, language,
                job_snap, resume_snap or {}, len(items))
            for i, it in enumerate(items):
                mode, cnt = it["followup_mode"], it["followup_count"]
                pl = planned.get(it["id"], [])
                if mode == "planned" and not pl:
                    mode = "none"
                target_n = {"none": 0, "fixed": cnt, "adaptive": cnt, "planned": len(pl)}[mode]
                await conn.execute(
                    """
                    INSERT INTO session_questions (session_id, source_item_id, kind, order_no, followup_no, followup_mode,
                           followup_target, planned_followups, category, difficulty, text, competency, expected_points, rubric)
                    VALUES ($1,$2,'main',$3,0,$4,$5,$6,$7,$8,$9,$10,$11,$12)
                    """, s["id"], it["id"], i + 1, mode, target_n, pl if mode == "planned" else None, it["category"],
                    it["difficulty"], it["text"], it["competency"], it["expected_points"] or [], it["rubric"])
            prefs = dict(u["preferences"] or {})
            prefs.update({"default_length": length_mode, "default_persona": persona, "default_language": language})
            await conn.execute("UPDATE users SET preferences = $2 WHERE id = $1", user_id, prefs)
            await conn.execute("UPDATE target_jobs SET last_practiced_at = now(), resume_id = COALESCE(resume_id, $2) WHERE id = $1",
                               target_id, r["id"] if r else None)
            await enqueue(conn, "synthesize_tts", {"session_id": str(s["id"])}, queue="interactive")
            await conn.execute(
                "INSERT INTO app_events (user_id, event_name, properties) VALUES ($1, 'interview.created', $2)",
                user_id, {"session_id": str(s["id"]), "length": length_mode, "persona": persona})
            qs = await conn.fetch("SELECT id, order_no, category FROM session_questions WHERE session_id = $1 ORDER BY order_no", s["id"])
            out = await session_out(conn, s)
    except asyncpg.UniqueViolationError:
        raise AppError(409, "ACTIVE_SESSION_EXISTS", "你有一場還沒結束的面試")
    return {"session": out, "questions": [{"id": str(q["id"]), "order_no": q["order_no"], "category": q["category"]} for q in qs]}


async def handle_tts(payload: dict) -> None:
    """主題目、開場白、接話短句的 TTS；全部完成後場次 ready（開發模式直接 ready）。"""
    sid = UUID(payload["session_id"])
    async with db.connection() as conn:
        s = await conn.fetchrow("SELECT * FROM interview_sessions WHERE id = $1", sid)
        if not s:
            return
        qs = await conn.fetch("SELECT * FROM session_questions WHERE session_id = $1 AND kind = 'main' AND tts_status = 'pending'", sid)
        name = await conn.fetchval("SELECT display_name FROM users WHERE id = $1", s["user_id"])
    async def one(q):
        try:
            key = await voice.synthesize(q["text"], s["language"], s["persona"], priority="interview",
                                         user_id=s["user_id"], session_id=sid)
            st = "ready" if key else "not_needed"
        except Exception as e:
            log.warning("tts_failed", question_id=str(q["id"]), error=str(e))
            key, st = None, "failed"
        async with db.connection() as c2:
            await c2.execute("UPDATE session_questions SET tts_audio_key = $2, tts_status = $3 WHERE id = $1", q["id"], key, st)
    await asyncio.gather(*(one(q) for q in qs))
    for q in await _planned_texts(sid):
        try:
            await voice.synthesize(q, s["language"], s["persona"], priority="normal", user_id=s["user_id"], session_id=sid)
        except Exception:
            pass
    try:
        await voice.synthesize(voice.intro_text(s["language"], _short(name)), s["language"], s["persona"], priority="interview")
        await voice.warm_cues(s["language"])
    except Exception as e:
        log.warning("intro_tts_failed", error=str(e))
    async with db.connection() as conn:
        await conn.execute("UPDATE interview_sessions SET status = 'ready', state_version = state_version + 1 WHERE id = $1 AND status = 'preparing'", sid)


async def _planned_texts(sid: UUID) -> list[str]:
    async with db.connection() as conn:
        rows = await conn.fetch("SELECT planned_followups FROM session_questions WHERE session_id = $1 AND planned_followups IS NOT NULL", sid)
    return [p["text"] for r in rows for p in (r["planned_followups"] or [])]


def _short(name: str | None) -> str:
    n = (name or "").strip()
    if not n:
        return "你好"
    if all(ord(c) > 0x2E80 for c in n) and len(n) >= 3:
        return n[-2:]
    return n.split()[0]


def dev_sample_answer(q) -> str:
    """開發模式（沒有 OpenAI 金鑰、瀏覽器也沒有語音辨識）：用範例回答讓評分與報告流程可以走完。"""
    return (f"（開發模式的範例回答，不是真正的轉錄）關於「{q['category']}」，我在上一份工作負責相關專案。"
            "嗯，我先釐清需求和限制，然後提出兩個方案比較取捨，最後選了比較好維護的做法，"
            "上線後回應時間從 800 毫秒降到 200 毫秒，客訴也少了三成。")


# ---------- 開始、播放完成、作答 ----------
async def start(user_id: UUID, sid: UUID, expected: int) -> dict:
    async with tx() as conn:
        s = await _lock(conn, user_id, sid)
        _check(s, expected, status=("ready",))
        first = await conn.fetchval("SELECT id FROM session_questions WHERE session_id = $1 AND kind = 'main' ORDER BY order_no LIMIT 1", sid)
        s = await _bump(conn, sid, status="in_progress", phase="asking", current_question_id=first, started_at=_now(),
                        last_heartbeat_at=_now())
        name = await conn.fetchval("SELECT display_name FROM users WHERE id = $1", user_id)
        await conn.execute("INSERT INTO app_events (user_id, event_name, properties) VALUES ($1, 'interview.started', $2)",
                           user_id, {"session_id": str(sid)})
        out = await session_out(conn, s)
    await limiter.mark_live(str(sid))
    return {"session": out, "intro": await voice.speech(voice.intro_text(s["language"], _short(name)), s["language"], s["persona"])}


async def playback_finished(user_id: UUID, sid: UUID, qid: UUID, expected: int, spoken_text: str | None,
                            fallback: str | None) -> dict:
    async with tx() as conn:
        s = await _lock(conn, user_id, sid)
        if s["phase"] == "awaiting_answer" and s["current_question_id"] == qid:
            return await session_out(conn, s)            # 重送：已處理
        _check(s, expected, status=("in_progress",), phase=("asking",))
        if s["current_question_id"] != qid:
            raise state_conflict(s["state_version"])
        await conn.execute(
            "UPDATE session_questions SET asked_at = COALESCE(asked_at, now()), status = CASE WHEN status = 'pending' THEN 'asked' ELSE status END, spoken_text = COALESCE($2, spoken_text) WHERE id = $1",
            qid, spoken_text)
        if fallback:
            await conn.execute(
                "INSERT INTO voice_events (session_id, session_question_id, source, event_type, payload) VALUES ($1,$2,'client',$3,'{}')",
                sid, qid, fallback[:40])
        s = await _bump(conn, sid, phase="awaiting_answer")
        return await session_out(conn, s)


async def _is_last_main(conn: db.Connection, q) -> bool:
    if q["kind"] != "main" or q["followup_mode"] != "none":
        return False
    later = await conn.fetchval("SELECT count(*) FROM session_questions WHERE session_id = $1 AND kind = 'main' AND order_no > $2",
                                q["session_id"], q["order_no"])
    return later == 1


async def begin_answer(user_id: UUID, sid: UUID, qid: UUID, expected: int) -> dict:
    async with tx() as conn:
        s = await _lock(conn, user_id, sid)
        _check(s, expected, status=("in_progress",), phase=("awaiting_answer",))
        if s["current_question_id"] != qid:
            raise state_conflict(s["state_version"])
        q = await conn.fetchrow("SELECT * FROM session_questions WHERE id = $1", qid)
        no = await conn.fetchval("SELECT COALESCE(max(attempt_no), 0) + 1 FROM answer_attempts WHERE session_question_id = $1", qid)
        a = await conn.fetchrow(
            "INSERT INTO answer_attempts (session_id, session_question_id, attempt_no, answer_started_at) VALUES ($1,$2,$3, now()) RETURNING *",
            sid, qid, no)
        s = await _bump(conn, sid, phase="answering", current_attempt_id=a["id"])
        ack = await voice.pick_ack(s["language"], await _is_last_main(conn, q))
        return {"attempt": {"id": str(a["id"]), "attempt_no": no, "answer_started_at": a["answer_started_at"]},
                "ack": ack, "session": await session_out(conn, s)}


async def discard(user_id: UUID, sid: UUID, aid: UUID, expected: int) -> dict:
    async with tx() as conn:
        s = await _lock(conn, user_id, sid)
        _check(s, expected, status=("in_progress",), phase=("answering",))
        if s["current_attempt_id"] != aid:
            raise state_conflict(s["state_version"])
        await conn.execute("UPDATE answer_attempts SET status = 'discarded', answer_ended_at = now() WHERE id = $1", aid)
        s = await _bump(conn, sid, phase="awaiting_answer", current_attempt_id=None)
        return await session_out(conn, s)


# ---------- 送出回答：追問規則 ----------
async def _next_main(conn: db.Connection, sid: UUID, after_order: int) -> db.Record | None:
    return await conn.fetchrow(
        "SELECT * FROM session_questions WHERE session_id = $1 AND kind = 'main' AND order_no > $2 ORDER BY order_no LIMIT 1",
        sid, after_order)


async def _previous_followups(conn: db.Connection, main_id: UUID) -> list[dict]:
    rows = await conn.fetch(
        """SELECT f.text, a.quick_transcript FROM session_questions f
             LEFT JOIN answer_attempts a ON a.session_question_id = f.id AND a.is_final
            WHERE f.parent_id = $1 ORDER BY f.followup_no""", main_id)
    return [{"question": r["text"], "answer": r["quick_transcript"] or ""} for r in rows]


async def decide_followup(s, main, transcript: str, *, skipped: bool) -> tuple[str, str, str | None] | None:
    """回傳 (text, followup_source, source_followup_id) 或 None（下一道主題目）。不在交易中呼叫（會呼叫 AI）。"""
    async with db.connection() as conn:
        asked = await conn.fetchval("SELECT count(*) FROM session_questions WHERE parent_id = $1", main["id"])
        previous = await _previous_followups(conn, main["id"])
    mode, target = main["followup_mode"], main["followup_target"] or 0
    if mode == "none" or asked >= target:
        return None
    if mode == "planned":
        pl = main["planned_followups"] or []
        if asked >= len(pl):
            return None
        return pl[asked]["text"], "planned", pl[asked].get("id")
    if mode == "fixed":
        q = None
        if transcript and not skipped:
            try:
                q = await asyncio.wait_for(fua.write(dict(main), transcript, previous, persona=s["persona"], language=s["language"],
                                                     user_id=s["user_id"], session_id=s["id"]),
                                           timeout=settings.followup_timeout_sec + 0.5)
            except Exception as e:
                log.info("followup_writer_fallback", error=str(e))
        if q:
            return q, "fixed_ai", None
        return fua.generic_followup(s["language"], asked, [p["question"] for p in previous]), "fixed_fallback", None
    # adaptive：跳過或沒有轉錄就不追問；Decider 逾時視為不追問
    if skipped or not transcript:
        return None
    try:
        q = await asyncio.wait_for(fua.decide(dict(main), transcript, previous, persona=s["persona"], language=s["language"],
                                              user_id=s["user_id"], session_id=s["id"]),
                                   timeout=settings.followup_timeout_sec + 0.5)
    except Exception as e:
        log.info("followup_decider_timeout", error=str(e))
        return None
    return (q, "adaptive_ai", None) if q else None


async def _advance(conn: db.Connection, s, main, decision, fu_tts_key: str | None) -> tuple[dict, db.Record]:
    """在交易中依決策推進：建立追問 → asking；或下一道主題目；或結束。回傳 (next, session)。"""
    if decision:
        text, source, src_id = decision
        no = await conn.fetchval("SELECT count(*) + 1 FROM session_questions WHERE parent_id = $1", main["id"])
        fq = await conn.fetchrow(
            """
            INSERT INTO session_questions (session_id, parent_id, kind, order_no, followup_no, followup_source, source_followup_id,
                   category, difficulty, text, rubric, tts_audio_key, tts_status)
            VALUES ($1,$2,'followup',$3,$4,$5,$6,$7,$8,$9,$10,$11,$12) RETURNING *
            """, s["id"], main["id"], main["order_no"], no, source, UUID(src_id) if src_id else None, main["category"],
            main["difficulty"], text, main["rubric"], fu_tts_key,
            "ready" if fu_tts_key else "pending" if settings.ai_enabled else "not_needed")
        s2 = await _bump(conn, s["id"], phase="asking", current_question_id=fq["id"], current_attempt_id=None)
        return {"type": "followup", "question": await _question_out(conn, fq, s["language"])}, s2
    nxt = await _next_main(conn, s["id"], main["order_no"])
    if nxt:
        s2 = await _bump(conn, s["id"], phase="asking", current_question_id=nxt["id"], current_attempt_id=None)
        return {"type": "question", "question": await _question_out(conn, nxt, s["language"])}, s2
    s2 = await _finish(conn, s, "completed")
    return {"type": "completed", "outro": await voice.speech(voice.CUES[voice.cues_lang(s["language"])]["outro"], s["language"])}, s2


async def _finish(conn: db.Connection, s, end_reason: str) -> db.Record:
    """結束場次：有作答 → completed＋報告；沒有 → aborted。"""
    answered = await conn.fetchval(
        "SELECT count(*) FROM answer_attempts WHERE session_id = $1 AND is_final AND status = 'saved'", s["id"])
    now = _now()
    if s["paused_at"]:
        await conn.execute("UPDATE interview_sessions SET paused_total_sec = paused_total_sec + $2, paused_at = NULL WHERE id = $1",
                           s["id"], int((now - s["paused_at"]).total_seconds()))
        s = await conn.fetchrow("SELECT * FROM interview_sessions WHERE id = $1", s["id"])
    status = "completed" if answered else "aborted"
    s2 = await _bump(conn, s["id"], status=status, phase="done", ended_at=now, end_reason=end_reason,
                     duration_sec=_elapsed({**dict(s), "ended_at": now, "paused_at": None}), current_attempt_id=None)
    if answered:
        await conn.execute(
            "INSERT INTO interview_reports (session_id, user_id, status) VALUES ($1, $2, 'processing') ON CONFLICT DO NOTHING",
            s["id"], s["user_id"])
        await enqueue(conn, "build_report", {"session_id": str(s["id"])}, dedupe_key=f"build_report:{s['id']}")
    await conn.execute("INSERT INTO app_events (user_id, event_name, properties) VALUES ($1, $2, $3)",
                       s["user_id"], f"interview.{status}", {"session_id": str(s["id"]), "end_reason": end_reason})
    await limiter.unmark_live(str(s["id"]))
    return s2


async def _followup_tts(s, decision) -> str | None:
    """只查快取（例如預設追問已在開始時合成）。沒有快取時不等合成：
    回應改帶串流網址，前端邊收邊播（voice.stream_question），省下 1～2 秒等待。"""
    if not decision:
        return None
    return await voice.cached_key(decision[0], s["language"], s["persona"])


async def complete(user_id: UUID, sid: UUID, aid: UUID, body: dict, idem_key: str) -> dict:
    fp = f"{aid}:{body['upload_id']}"
    cached = await redis.idem_begin(str(user_id), idem_key, fp)
    if cached:
        return cached["body"]
    try:
        result = await _complete(user_id, sid, aid, body, idem_key)
    except Exception:
        await redis.idem_abort(str(user_id), idem_key)
        raise
    await redis.idem_done(str(user_id), idem_key, fp, 200, json.loads(json.dumps(result, default=str)))
    return result


async def _complete(user_id: UUID, sid: UUID, aid: UUID, body: dict, idem_key: str) -> dict:
    # 1) 驗證狀態與上傳，標記 finalizing
    async with tx() as conn:
        s = await _lock(conn, user_id, sid)
        a = await conn.fetchrow("SELECT * FROM answer_attempts WHERE id = $1 AND session_id = $2", aid, sid)
        if not a:
            raise not_found()
        if a["status"] == "saved":                       # 重送（Redis 沒有紀錄時）：回傳目前狀態
            return await _replay(conn, s, a)
        if s["phase"] == "finalizing" and s["current_attempt_id"] == aid:
            pass                                          # 上次在轉錄時中斷：繼續
        else:
            _check(s, body.get("expected_version"), status=("in_progress",), phase=("answering",))
            if s["current_attempt_id"] != aid:
                raise state_conflict(s["state_version"])
            f = await uploads.consume(conn, user_id, UUID(body["upload_id"]), "answer_audio", aid)
            dur = body.get("client_duration_ms")
            await conn.execute(
                """UPDATE answer_attempts SET audio_key = $2, audio_mime = $3, audio_bytes = $4, audio_duration_ms = $5,
                          answer_ended_at = now(), idempotency_key = $6, upload_ticket_id = $7 WHERE id = $1""",
                aid, f["object_key"], f["content_type"], f["size"], int(dur) if dur else None, idem_key, UUID(body["upload_id"]))
            s = await _bump(conn, sid, phase="finalizing")
        a = await conn.fetchrow("SELECT * FROM answer_attempts WHERE id = $1", aid)
        q = await conn.fetchrow("SELECT * FROM session_questions WHERE id = $1", a["session_question_id"])
        main = q if q["kind"] == "main" else await conn.fetchrow("SELECT * FROM session_questions WHERE id = $1", q["parent_id"])
    # 2) 快速轉錄（同步、面試優先）
    transcript = ""
    try:
        from app.core import storage
        audio = await storage.read(a["audio_key"])
        transcript = await ai.transcribe(audio, f"answer.{(a['audio_mime'] or 'audio/webm').split('/')[-1]}", a["audio_mime"] or "audio/webm",
                                         fast=True, language=s["language"], user_id=user_id, session_id=sid,
                                         fake=lambda: (body.get("client_transcript") or "").strip() or dev_sample_answer(q))
    except Exception as e:
        log.warning("quick_stt_failed", attempt_id=str(aid), error=str(e))
    # 3) 依追問規則決定下一步（程式計數，AI 只寫內容）
    decision = await decide_followup(s, main, transcript, skipped=False)
    fu_key = await _followup_tts(s, decision)
    # 4) 交易：寫入作答、題目已回答、排最終轉錄、推進
    async with tx() as conn:
        s = await _lock(conn, user_id, sid)
        if s["phase"] != "finalizing" or s["current_attempt_id"] != aid:
            a = await conn.fetchrow("SELECT * FROM answer_attempts WHERE id = $1", aid)
            if a["status"] == "saved":
                return await _replay(conn, s, a)
            raise state_conflict(s["state_version"])
        await conn.execute("UPDATE answer_attempts SET is_final = false WHERE session_question_id = $1 AND is_final", q["id"])
        a = await conn.fetchrow(
            "UPDATE answer_attempts SET status = 'saved', is_final = true, quick_transcript = $2 WHERE id = $1 RETURNING *",
            aid, transcript or None)
        await conn.execute("UPDATE session_questions SET status = 'answered' WHERE id = $1", q["id"])
        await enqueue(conn, "transcribe_attempt", {"attempt_id": str(aid), "client_transcript": body.get("client_transcript")},
                      queue="interactive", dedupe_key=f"transcribe:{aid}")
        main = await conn.fetchrow("SELECT * FROM session_questions WHERE id = $1", main["id"])
        nxt, s = await _advance(conn, s, main, decision, fu_key)
        return {"attempt": {"id": str(aid), "status": "saved", "audio_duration_ms": a["audio_duration_ms"],
                            "quick_transcript": a["quick_transcript"]},
                "next": nxt, "session": await session_out(conn, s)}


async def _replay(conn: db.Connection, s, a) -> dict:
    """同一個回答重送：不重複寫入，依目前狀態回傳 next。"""
    if s["status"] in ("completed", "aborted"):
        nxt = {"type": "completed", "outro": await voice.speech(voice.CUES[voice.cues_lang(s["language"])]["outro"], s["language"])}
    else:
        q = await conn.fetchrow("SELECT * FROM session_questions WHERE id = $1", s["current_question_id"])
        nxt = {"type": "followup" if q["kind"] == "followup" else "question",
               "question": await _question_out(conn, q, s["language"])}
    return {"attempt": {"id": str(a["id"]), "status": a["status"], "audio_duration_ms": a["audio_duration_ms"],
                        "quick_transcript": a["quick_transcript"]}, "next": nxt, "session": await session_out(conn, s)}


async def skip(user_id: UUID, sid: UUID, qid: UUID, expected: int) -> dict:
    async with db.connection() as conn:
        s = await conn.fetchrow("SELECT * FROM interview_sessions WHERE id = $1 AND user_id = $2", sid, user_id)
        if not s:
            raise not_found()
        _check(s, expected, status=("in_progress",), phase=("asking", "awaiting_answer"))
        if s["current_question_id"] != qid:
            raise state_conflict(s["state_version"])
        q = await conn.fetchrow("SELECT * FROM session_questions WHERE id = $1", qid)
        main = q if q["kind"] == "main" else await conn.fetchrow("SELECT * FROM session_questions WHERE id = $1", q["parent_id"])
    # 跳過主題目 → 它的追問一併跳過；跳過追問 → fixed／planned 繼續計數，adaptive 結束追問
    decision = None if q["kind"] == "main" else await decide_followup(s, main, "", skipped=True)
    fu_key = await _followup_tts(s, decision)
    async with tx() as conn:
        s = await _lock(conn, user_id, sid)
        _check(s, expected, status=("in_progress",), phase=("asking", "awaiting_answer"))
        await conn.execute("UPDATE session_questions SET status = 'skipped' WHERE id = $1", qid)
        nxt, s = await _advance(conn, s, main, decision, fu_key)
        return {"attempt": None, "next": nxt, "session": await session_out(conn, s)}


# ---------- 暫停、繼續、心跳、結束 ----------
async def pause(user_id: UUID, sid: UUID, expected: int | None, reason: str) -> dict:
    async with tx() as conn:
        s = await _lock(conn, user_id, sid)
        if s["status"] == "paused":
            return {"session": await session_out(conn, s)}
        _check(s, None if reason == "page_leave" else expected, status=("in_progress", "ready"))
        if s["status"] == "ready":
            return {"session": await session_out(conn, s)}
        phase = s["phase"]
        if phase in ("answering",) and s["current_attempt_id"]:
            await conn.execute("UPDATE answer_attempts SET status = 'discarded', answer_ended_at = now() WHERE id = $1 AND status = 'answering'",
                               s["current_attempt_id"])
            phase = "awaiting_answer"
        # phase = finalizing（上傳中）不打斷，complete 完成後狀態自然推進
        s = await _bump(conn, sid, status="paused", phase=phase, paused_at=_now(),
                        current_attempt_id=s["current_attempt_id"] if phase == "finalizing" else None)
        await conn.execute("INSERT INTO voice_events (session_id, source, event_type, payload) VALUES ($1, 'server', 'paused', $2)",
                           sid, {"reason": reason})
    await limiter.unmark_live(str(sid))
    async with db.connection() as conn:
        return {"session": await session_out(conn, s)}


async def resume(user_id: UUID, sid: UUID, expected: int | None) -> dict:
    async with tx() as conn:
        s = await _lock(conn, user_id, sid)
        if s["status"] == "in_progress":
            return {"session": await session_out(conn, s)}
        _check(s, expected, status=("paused",))
        if _now() - s["paused_at"] > RESUME_WINDOW:
            await _finish(conn, s, "expired")
            raise state_conflict(None, "這場面試暫停超過 24 小時，已經自動結束")
        q = await conn.fetchrow("SELECT * FROM session_questions WHERE id = $1", s["current_question_id"]) if s["current_question_id"] else None
        phase = "awaiting_answer" if q and q["asked_at"] else "asking"
        s = await _bump(conn, sid, status="in_progress", phase=phase, paused_at=None, last_heartbeat_at=_now(),
                        paused_total_sec=s["paused_total_sec"] + int((_now() - s["paused_at"]).total_seconds()))
        out = await session_out(conn, s)
    await limiter.mark_live(str(sid))
    return {"session": out}


async def heartbeat(user_id: UUID, sid: UUID, events: list[dict]) -> None:
    async with db.connection() as conn:
        ok = await conn.fetchval(
            "UPDATE interview_sessions SET last_heartbeat_at = now() WHERE id = $1 AND user_id = $2 AND status = 'in_progress' RETURNING id",
            sid, user_id)
        for e in events[:20]:
            await conn.execute("INSERT INTO voice_events (session_id, source, event_type, payload) VALUES ($1, 'client', $2, $3)",
                               sid, str(e.get("type", "unknown"))[:40], e)
    if ok:
        await limiter.mark_live(str(sid))


async def end(user_id: UUID, sid: UUID, expected: int | None, reason: str) -> dict:
    async with tx() as conn:
        s = await _lock(conn, user_id, sid)
        if s["status"] in ("completed", "aborted"):
            rep = await conn.fetchrow("SELECT status FROM interview_reports WHERE session_id = $1", sid)
            return {"session": await session_out(conn, s), "report": {"status": rep["status"]} if rep else None}
        _check(s, expected, status=ACTIVE)
        if s["phase"] in ("answering", "finalizing") and s["current_attempt_id"]:
            await conn.execute("UPDATE answer_attempts SET status = 'interrupted' WHERE id = $1 AND status = 'answering'",
                               s["current_attempt_id"])
        s = await _finish(conn, s, "user_ended" if reason == "user_ended" else "completed")
        rep = await conn.fetchrow("SELECT status FROM interview_reports WHERE session_id = $1", sid)
        return {"session": await session_out(conn, s), "report": {"status": rep["status"]} if rep else None}


# ---------- 排程 ----------
async def pause_lost_sessions() -> int:
    async with db.connection() as conn:
        rows = await conn.fetch(
            """UPDATE interview_sessions SET status = 'paused', paused_at = now(), state_version = state_version + 1,
                      phase = CASE WHEN phase = 'answering' THEN 'awaiting_answer' ELSE phase END,
                      current_attempt_id = CASE WHEN phase = 'answering' THEN NULL ELSE current_attempt_id END
                WHERE status = 'in_progress' AND last_heartbeat_at < now() - interval '2 minutes'
            RETURNING id""")
        for r in rows:
            await conn.execute(
                "UPDATE answer_attempts SET status = 'discarded' WHERE session_id = $1 AND status = 'answering'", r["id"])
            await conn.execute("INSERT INTO voice_events (session_id, source, event_type, payload) VALUES ($1,'server','paused',$2)",
                               r["id"], {"reason": "connection_lost"})
            await limiter.unmark_live(str(r["id"]))
    return len(rows)


async def expire_paused_sessions() -> int:
    n = 0
    async with db.connection() as conn:
        ids = [r["id"] for r in await conn.fetch(
            "SELECT id FROM interview_sessions WHERE status = 'paused' AND paused_at < now() - interval '24 hours'")]
    for sid in ids:
        async with tx() as conn:
            s = await conn.fetchrow("SELECT * FROM interview_sessions WHERE id = $1 FOR UPDATE", sid)
            if s["status"] == "paused":
                await _finish(conn, s, "expired")
                n += 1
    return n
