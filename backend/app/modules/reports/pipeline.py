"""評分管線（flows.md §19）：最終轉錄 → 逐題評分（主題目與追問各自） → 整組分數 → 報告。"""
from uuid import UUID

import structlog

from app.ai import client as ai
from app.ai import text
from app.ai.agents import evaluation as ev
from app.core import db, redis, storage
from app.core.config import settings
from app.workers.queue import enqueue, tx

log = structlog.get_logger()


async def handle_transcribe(payload: dict) -> None:
    aid = UUID(payload["attempt_id"])
    async with db.connection() as conn:
        a = await conn.fetchrow(
            "SELECT a.*, s.language, s.user_id FROM answer_attempts a JOIN interview_sessions s ON s.id = a.session_id WHERE a.id = $1", aid)
    if not a or a["status"] != "saved":
        return
    client_t = (payload.get("client_transcript") or "").strip()
    try:
        audio = await storage.read(a["audio_key"])
        t = await ai.transcribe(audio, f"answer.{(a['audio_mime'] or 'audio/webm').split('/')[-1]}", a["audio_mime"] or "audio/webm",
                                fast=False, language=a["language"], user_id=a["user_id"], session_id=a["session_id"],
                                fake=lambda: client_t or a["quick_transcript"] or "", timeout=120)
        status = "done" if len(t.strip()) >= 5 else "low_quality"
    except Exception as e:
        log.warning("final_stt_failed", attempt_id=str(aid), error=str(e))
        t, status = a["quick_transcript"] or "", "failed" if not a["quick_transcript"] else "done"
    async with tx() as conn:
        await conn.execute(
            "UPDATE answer_attempts SET final_transcript = $2, transcript_status = $3, stt_model = $4 WHERE id = $1",
            aid, t, status, settings.stt_model if settings.ai_enabled else "fake")
        await conn.execute(
            """INSERT INTO evaluations (attempt_id, session_id, session_question_id) VALUES ($1, $2, $3)
               ON CONFLICT (attempt_id) DO NOTHING""", aid, a["session_id"], a["session_question_id"])
        await enqueue(conn, "evaluate_attempt", {"attempt_id": str(aid)}, dedupe_key=f"evaluate:{aid}")


async def handle_evaluate(payload: dict) -> None:
    aid = UUID(payload["attempt_id"])
    async with db.connection() as conn:
        a = await conn.fetchrow(
            """SELECT a.*, s.language, s.user_id, s.job_snapshot, s.resume_snapshot, s.status AS session_status,
                      q.text AS q_text, q.rubric, q.kind, q.parent_id, q.followup_no
                 FROM answer_attempts a JOIN interview_sessions s ON s.id = a.session_id
                 JOIN session_questions q ON q.id = a.session_question_id WHERE a.id = $1""", aid)
        if not a:
            return
        await conn.execute("UPDATE evaluations SET status = 'running' WHERE attempt_id = $1", aid)
        context: list[dict] = []
        if a["kind"] == "followup":    # 追問：主題目與前面的追問只當上下文
            rows = await conn.fetch(
                """SELECT q.text, q.followup_no, fa.final_transcript FROM session_questions q
                     LEFT JOIN answer_attempts fa ON fa.session_question_id = q.id AND fa.is_final
                    WHERE (q.id = $1 OR q.parent_id = $1) AND q.followup_no < $2 ORDER BY q.followup_no""",
                a["parent_id"], a["followup_no"])
            context = [{"題目": r["text"], "回答": r["final_transcript"] or ""} for r in rows]
    t = a["final_transcript"] or ""
    try:
        res = await ev.evaluate(question=a["q_text"], rubric=a["rubric"], transcript=t, job=a["job_snapshot"] or {},
                                resume=a["resume_snapshot"] or None, context=context, language=a["language"],
                                duration_ms=a["audio_duration_ms"], user_id=a["user_id"], session_id=a["session_id"])
    except Exception as e:
        async with db.connection() as conn:
            await conn.execute("UPDATE evaluations SET status = 'failed', error = $2, finished_at = now() WHERE attempt_id = $1",
                               aid, str(e)[:300])
        await _maybe_report(a["session_id"])
        raise
    o = res.out
    fillers, terms = text.filler_stats(t, a["language"])
    async with db.connection() as conn:
        await conn.execute(
            """UPDATE evaluations SET status = $2, score = $3, dimensions = $4, evidence = $5, strengths = $6, improvements = $7,
                      better_answer = $8, highlight = $9, filler_count = $10, filler_terms = $11, speech_rate = $12,
                      review_reason = $13, prompt_version = $14, model = $15, error = NULL, finished_at = now()
                WHERE attempt_id = $1""",
            aid, "needs_review" if res.needs_review else "done", o.score, o.dimensions.model_dump(), o.evidence,
            o.strengths, o.improvements, o.better_answer, o.highlight or None, fillers, terms,
            text.speech_rate(t, a["audio_duration_ms"], a["language"]), res.review_reason, ev.PV_EVAL,
            ai.model_for("reasoning") if settings.ai_enabled else "fake")
    await redis.publish(f"report:{a['session_id']}", "evaluation_done", {"question_id": str(a["session_question_id"]), "score": o.score})
    await _maybe_report(a["session_id"])


async def _maybe_report(sid: UUID) -> None:
    async with tx() as conn:
        s = await conn.fetchrow("SELECT status FROM interview_sessions WHERE id = $1", sid)
        if s and s["status"] in ("completed", "aborted"):
            await enqueue(conn, "build_report", {"session_id": str(sid)}, dedupe_key=f"build_report:{sid}")


async def handle_build_report(payload: dict) -> None:
    sid = UUID(payload["session_id"])
    async with redis.lock(f"report:{sid}", 60) as got:
        if not got:
            return
        async with db.connection() as conn:
            s = await conn.fetchrow("SELECT * FROM interview_sessions WHERE id = $1", sid)
            rep = await conn.fetchrow("SELECT * FROM interview_reports WHERE session_id = $1", sid)
            if not s or not rep or rep["status"] == "ready":
                return
            pending = await conn.fetchval(
                """SELECT count(*) FROM answer_attempts a LEFT JOIN evaluations e ON e.attempt_id = a.id
                    WHERE a.session_id = $1 AND a.is_final AND a.status = 'saved'
                      AND (e.id IS NULL OR e.status IN ('pending','running'))""", sid)
            if pending:
                return          # 還有題目在評分；最後一題評分完成時會再排一次
        await _build(s)


async def _build(s) -> None:
    sid = s["id"]
    w_main = settings.group_weight_main
    async with db.connection() as conn:
        mains = await conn.fetch(
            """SELECT m.id, m.order_no, m.category, m.status, em.score, em.improvements
                 FROM session_questions m
                 LEFT JOIN answer_attempts am ON am.session_question_id = m.id AND am.is_final AND am.status = 'saved'
                 LEFT JOIN evaluations em ON em.attempt_id = am.id AND em.status = 'done'
                WHERE m.session_id = $1 AND m.kind = 'main' ORDER BY m.order_no""", sid)
        fu = await conn.fetch(
            """SELECT f.parent_id, ef.score FROM session_questions f
                 JOIN answer_attempts af ON af.session_question_id = f.id AND af.is_final AND af.status = 'saved'
                 JOIN evaluations ef ON ef.attempt_id = af.id AND ef.status = 'done'
                WHERE f.session_id = $1 AND f.kind = 'followup'""", sid)
        stats = await conn.fetchrow(
            """SELECT avg((e.dimensions->>'structure')::int) AS structure, avg((e.dimensions->>'depth')::int) AS depth,
                      avg((e.dimensions->>'fluency')::int) AS fluency, avg((e.dimensions->>'job_fit')::int) AS job_fit,
                      avg((e.dimensions->>'confidence')::int) AS confidence, sum(e.filler_count) AS fillers,
                      avg(e.speech_rate) AS rate, avg(a.audio_duration_ms) AS avg_ms
                 FROM evaluations e JOIN answer_attempts a ON a.id = e.attempt_id
                WHERE e.session_id = $1 AND e.status = 'done'""", sid)
    by_parent: dict = {}
    for f in fu:
        by_parent.setdefault(f["parent_id"], []).append(f["score"])
    groups, items_for_ai = [], []
    for m in mains:
        if m["score"] is None:
            continue
        fus = by_parent.get(m["id"], [])
        avg = round(sum(fus) / len(fus)) if fus else None
        g = round(m["score"] * w_main + avg * (1 - w_main)) if fus else m["score"]
        groups.append((m, avg, len(fus), g))
        items_for_ai.append({"category": m["category"], "score": g, "improvement": (m["improvements"] or [""])[0]})
    overall = round(sum(g for *_, g in groups) / len(groups)) if groups else None
    dims = {k: round(float(stats[k])) for k in ev.DIM_KEYS if stats[k] is not None} if stats else {}
    try:
        if not groups:
            raise ValueError("沒有可評分的回答")
        agg = await ev.aggregate(items=items_for_ai, overall=overall, user_id=s["user_id"], session_id=sid)
        summary, focus = agg.summary, agg.focus_areas
    except Exception as e:
        log.warning("report_aggregate_failed", error=str(e))
        summary = f"這場整體 {overall} 分。" if overall is not None else "這場的錄音幾乎沒有內容，無法評分。可以在需要複查的題目按「重新評分」，或再練一次。"
        focus = []
    async with tx() as conn:
        for m, avg, n, g in groups:
            await conn.execute(
                """INSERT INTO question_group_scores (main_question_id, session_id, main_score, followup_avg, followup_count, group_score, weights)
                   VALUES ($1,$2,$3,$4,$5,$6,$7)
                   ON CONFLICT (main_question_id) DO UPDATE SET main_score = EXCLUDED.main_score, followup_avg = EXCLUDED.followup_avg,
                          followup_count = EXCLUDED.followup_count, group_score = EXCLUDED.group_score""",
                m["id"], sid, m["score"], avg, n, g, {"main": w_main, "followups": round(1 - w_main, 2)})
        prev = await conn.fetchrow(
            """SELECT s.id, r.overall_score FROM interview_sessions s JOIN interview_reports r ON r.session_id = s.id AND r.status = 'ready'
                WHERE s.target_job_id = $1 AND s.id <> $2 AND s.ended_at < $3 ORDER BY s.ended_at DESC LIMIT 1""",
            s["target_job_id"], sid, s["ended_at"])
        answered = sum(1 for m in mains if m["status"] == "answered")
        skipped = sum(1 for m in mains if m["status"] == "skipped")
        await conn.execute(
            """UPDATE interview_reports SET status = 'ready', overall_score = $2, prev_session_id = $3, delta_vs_prev = $4,
                      dimension_scores = $5, summary = $6, focus_areas = $7, filler_count = $8, avg_answer_sec = $9,
                      speech_rate = $10, answered_count = $11, skipped_count = $12, model = $13, finished_at = now(), error = NULL
                WHERE session_id = $1""",
            sid, overall, prev["id"] if prev else None,
            overall - prev["overall_score"] if prev and overall is not None else None, dims, summary, focus,
            int(stats["fillers"] or 0) if stats else 0, round(float(stats["avg_ms"]) / 1000) if stats and stats["avg_ms"] else None,
            round(float(stats["rate"]), 1) if stats and stats["rate"] else None, answered, skipped,
            ai.model_for("fast") if settings.ai_enabled else "fake")
        await conn.execute("INSERT INTO app_events (user_id, event_name, properties) VALUES ($1, 'report.ready', $2)",
                           s["user_id"], {"session_id": str(sid), "overall_score": overall})
    await redis.publish(f"report:{sid}", "report_ready", {"overall_score": overall})
