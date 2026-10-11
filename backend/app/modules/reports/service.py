"""面試報告：列表（含彙總）、詳情（主題目＋追問、整組分數）、錄音、弱項加入題組、重新評分、刪除。"""
import datetime as dt
from uuid import UUID

import structlog

from app.ai import text
from app.core import db, storage
from app.core.errors import AppError, not_found
from app.modules.questions import service as questions
from app.workers.queue import enqueue, tx

log = structlog.get_logger()
DIMS = [("structure", "內容結構", "開頭、經過、結果是否清楚"), ("depth", "專業深度", "技術細節與取捨"),
        ("fluency", "表達流暢", "贅詞、停頓與語速"), ("job_fit", "職缺契合", "回答與職缺需求的關聯"),
        ("confidence", "自信程度", "用詞是否肯定")]


def _label(job: dict) -> str:
    return "・".join(x for x in (job.get("company_name"), job.get("title")) if x)


async def list_(user_id: UUID, q: str | None, limit: int, offset: int) -> dict:
    async with db.connection() as conn:
        summary = await conn.fetchrow(
            """SELECT count(*) AS total, round(avg(r.overall_score) FILTER (WHERE r.status = 'ready')) AS avg_score,
                      max(r.overall_score) FILTER (WHERE r.status = 'ready') AS best_score
                 FROM interview_reports r JOIN interview_sessions s ON s.id = r.session_id WHERE s.user_id = $1""", user_id)
        rows = await conn.fetch(
            """SELECT s.id, s.ended_at, s.target_job_id, s.job_snapshot, s.persona, s.duration_sec, r.status AS report_status,
                      r.overall_score, r.delta_vs_prev,
                      (SELECT count(*) FROM session_questions m WHERE m.session_id = s.id AND m.kind = 'main' AND m.status = 'answered') AS answered_count
                 FROM interview_sessions s JOIN interview_reports r ON r.session_id = s.id
                WHERE s.user_id = $1 AND ($2::text IS NULL OR (s.job_snapshot->>'company_name') || ' ' || (s.job_snapshot->>'title') ILIKE '%' || $2 || '%')
                ORDER BY s.ended_at DESC NULLS LAST LIMIT $3 OFFSET $4""", user_id, q, limit + 1, offset)
    items = [{"id": str(r["id"]), "ended_at": r["ended_at"], "target_id": str(r["target_job_id"]),
              "job_label": _label(r["job_snapshot"] or {}), "answered_count": r["answered_count"], "persona": r["persona"],
              "duration_sec": r["duration_sec"], "report_status": r["report_status"], "overall_score": r["overall_score"],
              "delta_vs_prev": r["delta_vs_prev"]} for r in rows[:limit]]
    return {"summary": {"total": summary["total"], "avg_score": int(summary["avg_score"]) if summary["avg_score"] is not None else None,
                        "best_score": summary["best_score"]},
            "items": items, "next_cursor": str(offset + limit) if len(rows) > limit else None}


async def _session(conn, user_id: UUID, sid: UUID):
    s = await conn.fetchrow("SELECT * FROM interview_sessions WHERE id = $1 AND user_id = $2", sid, user_id)
    if not s:
        raise not_found("找不到這份報告")
    return s


def _eval_out(e) -> dict | None:
    if not e or e["e_status"] is None:
        return None
    return {"status": e["e_status"], "score": e["score"], "highlight": e["highlight"], "strengths": e["strengths"] or [],
            "improvements": e["improvements"] or [], "better_answer": e["better_answer"], "evidence": e["evidence"] or [],
            "review_reason": e["review_reason"], "dimensions": e["dimensions"]}


def _answer_out(e) -> dict | None:
    if not e or not e["attempt_id"]:
        return None
    return {"attempt_id": str(e["attempt_id"]), "transcript": e["final_transcript"] or e["quick_transcript"] or "",
            "transcript_status": e["transcript_status"], "duration_ms": e["audio_duration_ms"],
            "has_audio": bool(e["audio_key"])}


async def detail(user_id: UUID, sid: UUID) -> dict:
    async with db.connection() as conn:
        s = await _session(conn, user_id, sid)
        rep = await conn.fetchrow("SELECT * FROM interview_reports WHERE session_id = $1", sid)
        if not rep:
            raise not_found("這場面試沒有產生報告")
        rows = await conn.fetch(
            """SELECT q.*, a.id AS attempt_id, a.final_transcript, a.quick_transcript, a.transcript_status, a.audio_duration_ms,
                      a.audio_key, e.status AS e_status, e.score, e.highlight, e.strengths, e.improvements, e.better_answer,
                      e.evidence, e.review_reason, e.dimensions, g.group_score, g.main_score, g.followup_avg, g.weights
                 FROM session_questions q
                 LEFT JOIN answer_attempts a ON a.session_question_id = q.id AND a.is_final AND a.status = 'saved'
                 LEFT JOIN evaluations e ON e.attempt_id = a.id
                 LEFT JOIN question_group_scores g ON g.main_question_id = q.id
                WHERE q.session_id = $1 ORDER BY q.order_no, q.followup_no""", sid)
        prog = await conn.fetchrow(
            """SELECT count(*) FILTER (WHERE e.status IN ('done','needs_review','failed')) AS evaluated, count(*) AS total
                 FROM answer_attempts a LEFT JOIN evaluations e ON e.attempt_id = a.id
                WHERE a.session_id = $1 AND a.is_final AND a.status = 'saved'""", sid)
    items, by_id = [], {}
    for r in rows:
        if r["kind"] == "main":
            st = r["status"] if r["status"] in ("answered", "skipped") else "unanswered"
            it = {"question_id": str(r["id"]), "order_no": r["order_no"], "category": r["category"], "status": st,
                  "followup_mode": r["followup_mode"], "question_text": r["text"],
                  "group": {"score": r["group_score"], "main_score": r["main_score"], "followup_avg": r["followup_avg"],
                            "weights": r["weights"]} if r["group_score"] is not None else None,
                  "answer": _answer_out(r), "evaluation": _eval_out(r), "followups": []}
            items.append(it)
            by_id[r["id"]] = it
        elif r["parent_id"] in by_id:
            by_id[r["parent_id"]]["followups"].append(
                {"question_id": str(r["id"]), "followup_no": r["followup_no"], "followup_source": r["followup_source"],
                 "status": r["status"], "question_text": r["spoken_text"] or r["text"], "answer": _answer_out(r),
                 "evaluation": _eval_out(r)})
    dims = rep["dimension_scores"] or {}
    job = s["job_snapshot"] or {}
    return {
        "status": rep["status"],
        "session": {"id": str(s["id"]), "ended_at": s["ended_at"], "persona": s["persona"], "language": s["language"],
                    "length_mode": s["length_mode"], "duration_sec": s["duration_sec"], "target_id": str(s["target_job_id"]),
                    "job_label": _label(job), "company_name": job.get("company_name")},
        "overall_score": rep["overall_score"], "delta_vs_prev": rep["delta_vs_prev"], "summary": rep["summary"],
        "dimensions": [{"key": k, "label": l, "hint": h, "score": dims.get(k)} for k, l, h in DIMS],
        "metrics": {"filler_count": rep["filler_count"], "avg_answer_sec": rep["avg_answer_sec"], "speech_rate": rep["speech_rate"],
                    "speech_rate_unit": "words_per_min" if s["language"] == "en" else "chars_per_min"},
        "focus_areas": rep["focus_areas"] or [],
        "progress": {"evaluated": prog["evaluated"], "total": prog["total"]},
        "items": items,
    }


async def audio_url(user_id: UUID, sid: UUID, aid: UUID) -> dict:
    async with db.connection() as conn:
        await _session(conn, user_id, sid)
        a = await conn.fetchrow("SELECT audio_key, audio_mime FROM answer_attempts WHERE id = $1 AND session_id = $2", aid, sid)
    if not a:
        raise not_found()
    if not a["audio_key"]:
        raise AppError(410, "GONE", "這段錄音已超過保留期限")
    return {"url": storage.presign_get(a["audio_key"], 300, a["audio_mime"]), "expires_in": 300}


async def add_to_set(user_id: UUID, sid: UUID, ids: list[UUID] | None) -> dict:
    async with tx() as conn:
        s = await _session(conn, user_id, sid)
        set_id = await conn.fetchval("SELECT id FROM question_sets WHERE target_job_id = $1", s["target_job_id"])
        rows = await conn.fetch(
            """SELECT q.*, g.group_score FROM session_questions q LEFT JOIN question_group_scores g ON g.main_question_id = q.id
                WHERE q.session_id = $1 AND q.kind = 'main'
                  AND (($2::uuid[] IS NOT NULL AND q.id = ANY($2)) OR ($2::uuid[] IS NULL AND g.group_score < 80))
                ORDER BY q.order_no""", sid, ids)
        existing = [r["text"] for r in await conn.fetch("SELECT text FROM question_set_items WHERE set_id = $1 AND deleted_at IS NULL", set_id)]
        new, skipped = [], 0
        for r in rows:
            if text.is_duplicate(r["text"], existing):
                same = await conn.fetchval(
                    "SELECT id FROM question_set_items WHERE set_id = $1 AND deleted_at IS NULL AND similarity(text, $2) > 0.3 ORDER BY similarity(text, $2) DESC LIMIT 1",
                    set_id, r["text"])
                if same:   # 已在題組：移到最前面
                    await conn.execute("UPDATE question_set_items SET order_no = 0 WHERE id = $1", same)
                skipped += 1
                continue
            existing.append(r["text"])
            new.append({"category": r["category"], "difficulty": r["difficulty"], "text": r["text"], "competency": r["competency"],
                        "expected_points": r["expected_points"], "rubric": r["rubric"],
                        "source_reason": "上次面試分數較低，優先再練", "followup_mode": r["followup_mode"] or "adaptive",
                        "followup_count": max(1, min(3 if r["followup_mode"] == "fixed" else 2, r["followup_target"] or 1))})
        await questions.append_items(conn, set_id, new, source="report", at_front=True)
        await conn.execute(
            """UPDATE question_set_items i SET order_no = x.rn FROM (
                 SELECT id, row_number() OVER (ORDER BY order_no, created_at) AS rn FROM question_set_items
                  WHERE set_id = $1 AND deleted_at IS NULL) x WHERE i.id = x.id""", set_id)
    return {"target_id": str(s["target_job_id"]), "added": len(new), "skipped_duplicates": skipped}


async def re_evaluate(user_id: UUID, sid: UUID, qid: UUID) -> dict:
    async with tx() as conn:
        await _session(conn, user_id, sid)
        e = await conn.fetchrow(
            """SELECT e.* FROM evaluations e JOIN answer_attempts a ON a.id = e.attempt_id
                WHERE e.session_question_id = $1 AND e.session_id = $2 AND a.is_final""", qid, sid)
        if not e or e["status"] not in ("failed", "needs_review"):
            raise AppError(409, "STATE_CONFLICT", "這題不需要重新評分")
        await conn.execute("UPDATE evaluations SET status = 'pending' WHERE id = $1", e["id"])
        await conn.execute("UPDATE interview_reports SET status = 'processing' WHERE session_id = $1", sid)
        await enqueue(conn, "transcribe_attempt", {"attempt_id": str(e["attempt_id"])}, queue="interactive",
                      dedupe_key=f"transcribe:{e['attempt_id']}")
    return {"status": "pending"}


async def delete(user_id: UUID, sid: UUID) -> None:
    async with db.transaction() as conn:
        s = await _session(conn, user_id, sid)
        if s["status"] in ("preparing", "ready", "in_progress", "paused"):
            raise AppError(409, "ACTIVE_SESSION_EXISTS", "面試還沒結束，先結束再刪除", {"session_id": str(sid)})
        keys = [r["audio_key"] for r in await conn.fetch("SELECT audio_key FROM answer_attempts WHERE session_id = $1 AND audio_key IS NOT NULL", sid)]
        await conn.execute("UPDATE upload_tickets SET attempt_id = NULL WHERE attempt_id IN (SELECT id FROM answer_attempts WHERE session_id = $1)", sid)
        await conn.execute("DELETE FROM interview_sessions WHERE id = $1", sid)
    try:
        await storage.delete(*keys)
    except Exception as e:
        log.warning("audio_delete_failed", error=str(e))


async def delete_all(user_id: UUID) -> int:
    async with db.connection() as conn:
        ids = [r["id"] for r in await conn.fetch(
            "SELECT id FROM interview_sessions WHERE user_id = $1 AND status IN ('completed','aborted')", user_id)]
    for sid in ids:
        await delete(user_id, sid)
    return len(ids)


def iso(v):
    return v.isoformat() if isinstance(v, dt.datetime) else v
