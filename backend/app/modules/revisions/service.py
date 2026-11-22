"""履歷修改：設定 → Ava 產生挖掘卡片 → 使用者逐張回答 → 產生新版履歷（逐條標出改動）→ 逐條接受／還原／編輯 → Word、存成新履歷。

選了職缺才依職缺修；沒選就做整體改善（見 spec/features/resume-revision.md）。
"""
import hashlib
import io
import re
import uuid
from uuid import UUID

import structlog

from app.ai.agents import revision as rv
from app.core import db, redis, storage
from app.core.config import settings
from app.core.errors import AppError, not_found
from app.modules.uploads import service as uploads
from app.workers.queue import enqueue, tx

log = structlog.get_logger()
MAX_TARGETS = 3
MAX_CARDS = 10
CHANGES = ("keep", "edit", "new")
KINDS = ("gap", "quantify", "missing", "clarify")
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PLACEHOLDER = re.compile(r"\[[^\[\]\n]{1,40}\]")


# ---------- 建立 ----------
async def create(user_id: UUID, resume_id: UUID, target_ids: list[UUID], setup: dict) -> dict:
    target_ids = list(dict.fromkeys(target_ids))
    if len(target_ids) > MAX_TARGETS:
        raise AppError(400, "VALIDATION_ERROR", f"最多選 {MAX_TARGETS} 個職缺", {"fields": {"target_ids": "count"}})
    await redis.rate_limit("resume_revision", str(user_id))
    async with tx() as conn:
        r = await conn.fetchrow("SELECT id, parse_status FROM resumes WHERE id = $1 AND user_id = $2 AND deleted_at IS NULL", resume_id, user_id)
        if not r:
            raise not_found("找不到這份履歷")
        if r["parse_status"] in ("pending", "parsing"):
            raise AppError(409, "RESOURCE_NOT_READY", "履歷還在解析，完成後再開始")
        if r["parse_status"] != "parsed":
            raise AppError(400, "VALIDATION_ERROR", "這份履歷沒有解析成功，請換一份或重新上傳", {"fields": {"resume_id": "unparsed"}})
        job_ids = []
        if target_ids:
            rows = await conn.fetch(
                """SELECT t.id, j.id AS job_id, j.parse_status FROM target_jobs t JOIN job_posts j ON j.id = t.job_post_id
                    WHERE t.id = ANY($1::uuid[]) AND t.user_id = $2 AND t.status = 'active' AND j.deleted_at IS NULL""",
                target_ids, user_id)
            if len(rows) != len(target_ids):
                raise not_found("找不到部分職缺，請重新整理後再選")
            if any(x["parse_status"] in ("pending", "parsing") for x in rows):
                raise AppError(409, "RESOURCE_NOT_READY", "有職缺還在整理內容，完成後再開始")
            by_id = {x["id"]: x["job_id"] for x in rows}
            job_ids = [by_id[t] for t in target_ids]
        rid = await conn.fetchval(
            "INSERT INTO resume_revisions (user_id, resume_id, target_job_ids, setup) VALUES ($1,$2,$3,$4) RETURNING id",
            user_id, resume_id, job_ids, setup)
        await enqueue(conn, "build_revision_cards", {"revision_id": str(rid)}, queue="interactive", dedupe_key=f"revision_cards:{rid}")
    return await get(user_id, rid)


# ---------- 讀取 ----------
def final_lines(result: dict, decisions: dict) -> dict:
    """套用使用者的決定：還原的新增條目拿掉、還原的改寫換回原文、改過的用使用者的文字。回傳同結構、只剩 text 的新版履歷。"""
    sections = []
    for s in result.get("sections") or []:
        entries = []
        for e in s["entries"]:
            lines = []
            for ln in e["lines"]:
                d = decisions.get(ln["id"]) or {}
                if d.get("accept") is False:
                    if ln["change"] == "new":
                        continue
                    text = ln["original"] if ln["change"] == "edit" else ln["text"]
                else:
                    text = d.get("text") or ln["text"]
                if text.strip():
                    lines.append(text.strip())
            if lines or e["title"]:
                entries.append({"title": e["title"], "meta": e["meta"], "lines": lines})
        if entries:
            sections.append({"title": s["title"], "entries": entries})
    return {"name": result.get("name") or "", "headline": result.get("headline") or "", "contact": result.get("contact") or "",
            "sections": sections}


def _placeholders(doc: dict) -> int:
    texts = [doc["headline"]] + [x for s in doc["sections"] for e in s["entries"] for x in [e["title"], e["meta"], *e["lines"]]]
    return sum(len(PLACEHOLDER.findall(t)) for t in texts)


async def _out(conn: db.Connection, r) -> dict:
    jobs = await conn.fetch(
        """SELECT j.company_name, j.title FROM unnest($1::uuid[]) WITH ORDINALITY AS x(id, n)
             JOIN job_posts j ON j.id = x.id ORDER BY x.n""", r["target_job_ids"]) if r["target_job_ids"] else []
    resume = await conn.fetchrow("SELECT id, filename, parsed_json FROM resumes WHERE id = $1", r["resume_id"]) if r["resume_id"] else None
    saved = await conn.fetchrow("SELECT id, filename, parse_status FROM resumes WHERE id = $1 AND deleted_at IS NULL",
                                r["saved_resume_id"]) if r["saved_resume_id"] else None
    final = final_lines(r["result"], r["decisions"]) if r["result"] else None
    return {
        "id": str(r["id"]), "status": r["status"], "setup": r["setup"], "error": r["error"],
        "resume": {"id": str(resume["id"]), "filename": resume["filename"]} if resume else None,
        "jobs": [{"company_name": j["company_name"], "title": j["title"]} for j in jobs],
        "cards": r["cards"], "answers": r["answers"], "result": r["result"], "decisions": r["decisions"],
        "placeholders": _placeholders(final) if final else 0,
        "saved_resume": {"id": str(saved["id"]), "filename": saved["filename"], "parse_status": saved["parse_status"]} if saved else None,
        "created_at": r["created_at"], "finished_at": r["finished_at"],
    }


async def _owned(conn: db.Connection, user_id: UUID, rid: UUID, lock: bool = False) -> db.Record:
    r = await conn.fetchrow(f"SELECT * FROM resume_revisions WHERE id = $1 AND user_id = $2{' FOR UPDATE' if lock else ''}", rid, user_id)
    if not r:
        raise not_found("找不到這次履歷修改")
    return r


async def get(user_id: UUID, rid: UUID) -> dict:
    async with db.connection() as conn:
        return await _out(conn, await _owned(conn, user_id, rid))


async def list_(user_id: UUID) -> list[dict]:
    async with db.connection() as conn:
        rs = await conn.fetch(
            """SELECT v.id, v.status, v.setup, v.created_at, v.target_job_ids, r.filename AS resume,
                      COALESCE(jsonb_array_length(v.cards), 0) AS card_count,
                      (SELECT count(*) FROM jsonb_object_keys(v.answers)) AS answered
                 FROM resume_revisions v LEFT JOIN resumes r ON r.id = v.resume_id
                WHERE v.user_id = $1 ORDER BY v.created_at DESC LIMIT 30""", user_id)
        names = dict(await conn.fetch("SELECT id, company_name FROM job_posts WHERE id = ANY($1::uuid[])",
                                      list({j for r in rs for j in r["target_job_ids"]}))) if rs else {}
    return [{"id": str(r["id"]), "status": r["status"], "goal": r["setup"].get("goal"), "created_at": r["created_at"],
             "resume": r["resume"], "companies": [names[j] for j in r["target_job_ids"] if j in names],
             "card_count": r["card_count"], "answered": r["answered"]} for r in rs]


# ---------- 回答卡片 ----------
async def put_answers(user_id: UUID, rid: UUID, answers: dict[str, dict]) -> dict:
    """存目前的回答（整份取代，前端每答一張就送一次）。"""
    async with tx() as conn:
        r = await _owned(conn, user_id, rid, lock=True)
        if r["status"] != "answering":
            raise AppError(409, "STATE_CONFLICT", "這次修改已經不能再改回答了")
        n = len(r["cards"] or [])
        clean = {}
        for k, a in answers.items():
            if not k.isdigit() or int(k) >= n:
                continue
            choice = a.get("choice")
            labels = [o["label"] for o in r["cards"][int(k)]["options"]]
            clean[k] = {"choice": choice if choice in labels else None, "text": (a.get("text") or "").strip()[:1000],
                        "skipped": bool(a.get("skipped"))}
        await conn.execute("UPDATE resume_revisions SET answers = $2 WHERE id = $1", rid, clean)
        r = await conn.fetchrow("SELECT * FROM resume_revisions WHERE id = $1", rid)
        return await _out(conn, r)


async def generate(user_id: UUID, rid: UUID) -> dict:
    """答完卡片 → 產生新版履歷。失敗的可以再按一次：卡片沒產生成功就重產卡片，否則重產履歷。"""
    async with tx() as conn:
        r = await _owned(conn, user_id, rid, lock=True)
        if r["status"] in ("carding", "generating"):
            return await _out(conn, r)
        if r["status"] == "ready":
            raise AppError(409, "STATE_CONFLICT", "這次修改已經完成了")
        if r["status"] == "failed" and not r["cards"]:
            await conn.execute("UPDATE resume_revisions SET status = 'carding', error = NULL WHERE id = $1", rid)
            await enqueue(conn, "build_revision_cards", {"revision_id": str(rid)}, queue="interactive", dedupe_key=f"revision_cards:{rid}")
        else:
            await conn.execute("UPDATE resume_revisions SET status = 'generating', error = NULL WHERE id = $1", rid)
            await enqueue(conn, "build_revision", {"revision_id": str(rid)}, queue="interactive", dedupe_key=f"revision:{rid}")
        return await _out(conn, await conn.fetchrow("SELECT * FROM resume_revisions WHERE id = $1", rid))


# ---------- 結果 ----------
async def put_decisions(user_id: UUID, rid: UUID, decisions: dict[str, dict]) -> dict:
    async with tx() as conn:
        r = await _owned(conn, user_id, rid, lock=True)
        if r["status"] != "ready":
            raise AppError(409, "STATE_CONFLICT", "新版履歷還沒產生")
        ids = {ln["id"] for s in r["result"]["sections"] for e in s["entries"] for ln in e["lines"]}
        clean = {}
        for k, d in decisions.items():
            if k not in ids:
                continue
            item = {"accept": d.get("accept") is not False}
            if text := (d.get("text") or "").strip()[:1000]:
                item["text"] = text
            if not item["accept"] or "text" in item:
                clean[k] = item
        await conn.execute("UPDATE resume_revisions SET decisions = $2 WHERE id = $1", rid, clean)
        return await _out(conn, await conn.fetchrow("SELECT * FROM resume_revisions WHERE id = $1", rid))


def _filename(r) -> str:
    base = (r["result"].get("name") or "履歷").strip()[:40]
    return f"{base}_履歷_{r['created_at']:%Y%m%d}.docx"


def build_docx(doc: dict) -> bytes:
    import docx
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt, RGBColor

    d = docx.Document()
    style = d.styles["Normal"]
    style.font.size = Pt(10.5)
    style.font.name = "Microsoft JhengHei"
    # 中文字型要另外設定 eastAsia，否則 Word 會用預設的新細明體
    style.element.rPr.rFonts.set("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}eastAsia", "Microsoft JhengHei")
    for sec in d.sections:
        sec.top_margin = sec.bottom_margin = Pt(42)
        sec.left_margin = sec.right_margin = Pt(50)
    p = d.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run(doc["name"] or "履歷")
    run.bold = True
    run.font.size = Pt(20)
    for extra in (doc["headline"], doc["contact"]):
        if extra:
            q = d.add_paragraph(extra)
            q.alignment = WD_ALIGN_PARAGRAPH.CENTER
            q.paragraph_format.space_after = Pt(2)
    for s in doc["sections"]:
        hd = d.add_paragraph()
        hd.paragraph_format.space_before = Pt(12)
        hr = hd.add_run(s["title"])
        hr.bold = True
        hr.font.size = Pt(13)
        hr.font.color.rgb = RGBColor(0x1F, 0x3A, 0x5F)
        for e in s["entries"]:
            if e["title"] or e["meta"]:
                t = d.add_paragraph()
                t.paragraph_format.space_before = Pt(6)
                t.paragraph_format.space_after = Pt(2)
                if e["title"]:
                    t.add_run(e["title"]).bold = True
                if e["meta"]:
                    m = t.add_run(f"　{e['meta']}")
                    m.font.color.rgb = RGBColor(0x66, 0x66, 0x66)
            flat = not e["title"] and len(e["lines"]) == 1
            for line in e["lines"]:
                para = d.add_paragraph(line) if flat else d.add_paragraph(line, style="List Bullet")
                para.paragraph_format.space_after = Pt(1)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


async def docx_file(user_id: UUID, rid: UUID) -> tuple[bytes, str]:
    async with db.connection() as conn:
        r = await _owned(conn, user_id, rid)
    if r["status"] != "ready":
        raise AppError(409, "STATE_CONFLICT", "新版履歷還沒產生")
    return build_docx(final_lines(r["result"], r["decisions"])), _filename(r)


async def save(user_id: UUID, rid: UUID, set_primary: bool) -> dict:
    """存成新的一份履歷（Word），走一般的解析流程；原本那份不動。"""
    async with db.connection() as conn:
        r = await _owned(conn, user_id, rid)
    if r["status"] != "ready":
        raise AppError(409, "STATE_CONFLICT", "新版履歷還沒產生")
    data = build_docx(final_lines(r["result"], r["decisions"]))
    new_id = uuid.uuid4()
    key = f"users/{user_id}/resumes/{new_id}.docx"
    await storage.put(key, data, DOCX)
    try:
        async with tx() as conn:
            await conn.execute("SELECT 1 FROM users WHERE id = $1 FOR UPDATE", user_id)
            if await uploads.resume_count(conn, user_id) >= settings.resume_max_count:
                raise uploads.resume_limit_error()
            if set_primary:
                await conn.execute("UPDATE resumes SET is_primary = false WHERE user_id = $1 AND is_primary", user_id)
            primary = set_primary or not await conn.fetchval(
                "SELECT 1 FROM resumes WHERE user_id = $1 AND is_primary AND deleted_at IS NULL", user_id)
            await conn.execute(
                """INSERT INTO resumes (id, user_id, filename, mime_type, size_bytes, file_key, sha256, is_primary)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8)""",
                new_id, user_id, _filename(r), DOCX, len(data), key, hashlib.sha256(data).hexdigest(), primary)
            await conn.execute("UPDATE resume_revisions SET saved_resume_id = $2 WHERE id = $1", rid, new_id)
            await enqueue(conn, "parse_resume", {"resume_id": str(new_id)}, dedupe_key=f"parse_resume:{new_id}")
    except Exception:
        try:
            await storage.delete(key)
        except Exception as e:
            log.warning("revision_file_cleanup_failed", error=str(e))
        raise
    return await get(user_id, rid)


# ---------- worker ----------
async def _inputs(conn: db.Connection, r) -> tuple[dict | None, str | None, list[dict]]:
    res = await conn.fetchrow("SELECT parsed_json, parsed_text FROM resumes WHERE id = $1", r["resume_id"]) if r["resume_id"] else None
    jobs = await conn.fetch(
        """SELECT j.* FROM unnest($1::uuid[]) WITH ORDINALITY AS x(id, n) JOIN job_posts j ON j.id = x.id
            WHERE j.deleted_at IS NULL ORDER BY x.n""", r["target_job_ids"]) if r["target_job_ids"] else []
    return (res["parsed_json"] if res else None, res["parsed_text"] if res else None,
            [{"company_name": j["company_name"], "title": j["title"], "duties": list(j["duties"] or []),
              "requirements": list(j["requirements"] or []), "tags": list(j["tags"] or [])} for j in jobs])


async def _fail(rid: UUID, e: Exception) -> None:
    log.warning("revision_failed", revision_id=str(rid), error=repr(e))
    async with db.connection() as conn:
        await conn.execute("UPDATE resume_revisions SET status = 'failed', error = $2 WHERE id = $1", rid, str(e)[:300] or "產生失敗")


async def handle_cards(payload: dict) -> None:
    rid = UUID(payload["revision_id"])
    async with db.connection() as conn:
        r = await conn.fetchrow("SELECT * FROM resume_revisions WHERE id = $1 AND status = 'carding'", rid)
        if not r:
            return
        parsed, raw, jobs = await _inputs(conn, r)
    try:
        out = await rv.build_cards(setup=r["setup"], parsed=parsed, raw=raw, jobs=jobs, user_id=r["user_id"])
    except Exception as e:
        return await _fail(rid, e)
    cards = []
    for c in out.cards:
        kind = c.kind if c.kind in KINDS else "missing"
        if kind == "gap" and not jobs:          # 沒選職缺不該有缺口卡
            continue
        opts = [{"label": o.label.strip()[:16], "opens_detail": o.opens_detail} for o in c.options[:4] if o.label.strip()]
        cards.append({"kind": kind, "context": c.context.strip()[:80], "question": c.question.strip()[:100], "options": opts,
                      "detail_prompt": c.detail_prompt.strip()[:80], "example": c.example.strip()[:100]})
    if not cards:
        return await _fail(rid, RuntimeError("Ava 沒有想到要問的問題，請再試一次"))
    async with db.connection() as conn:
        await conn.execute("UPDATE resume_revisions SET status = 'answering', cards = $2 WHERE id = $1 AND status = 'carding'",
                           rid, cards[:MAX_CARDS])


def _clean_result(out: rv.RevisionOut, n_cards: int) -> dict:
    sections = []
    for si, s in enumerate(out.sections[:8]):
        entries = []
        for ei, e in enumerate(s.entries[:12]):
            lines = []
            for li, ln in enumerate(e.lines[:12]):
                if not ln.text.strip():
                    continue
                change = ln.change if ln.change in CHANGES else "keep"
                if change == "edit" and not ln.original.strip():
                    change = "keep"
                lines.append({"id": f"s{si}e{ei}l{li}", "text": ln.text.strip()[:600], "change": change,
                              "original": ln.original.strip()[:600] if change == "edit" else "",
                              "reason": ln.reason.strip()[:80] if change != "keep" else "",
                              "card": ln.card if change != "keep" and 0 <= ln.card < n_cards else -1})
            entries.append({"title": e.title.strip()[:120], "meta": e.meta.strip()[:80], "lines": lines})
        sections.append({"title": s.title.strip()[:30] or "其他", "entries": entries})
    return {"name": out.name.strip()[:60], "headline": out.headline.strip()[:120], "contact": out.contact.strip()[:200],
            "sections": sections, "summary": out.summary.strip()[:200], "tips": [t.strip()[:80] for t in out.tips[:3] if t.strip()]}


async def handle_build(payload: dict) -> None:
    rid = UUID(payload["revision_id"])
    async with db.connection() as conn:
        r = await conn.fetchrow("SELECT * FROM resume_revisions WHERE id = $1 AND status = 'generating'", rid)
        if not r:
            return
        parsed, raw, jobs = await _inputs(conn, r)
    try:
        out = await rv.build_resume(setup=r["setup"], parsed=parsed, raw=raw, jobs=jobs, cards=r["cards"] or [], answers=r["answers"],
                                    user_id=r["user_id"])
    except Exception as e:
        return await _fail(rid, e)
    async with db.connection() as conn:
        await conn.execute(
            """UPDATE resume_revisions SET status = 'ready', result = $2, decisions = '{}', prompt_version = $3, finished_at = now()
                WHERE id = $1 AND status = 'generating'""", rid, _clean_result(out, len(r["cards"] or [])), rv.PV_RESUME)
