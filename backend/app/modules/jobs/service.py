"""職缺：職缺搜尋（平台職缺，只回傳已上架）、詳情、使用者貼上職缺的解析（parse_job worker）。"""
import datetime as dt
from uuid import UUID

from app.ai import client as ai
from app.ai.agents import parsing
from app.core import db
from app.core.errors import not_found
from app.workers.queue import enqueue, tx

SITE_LABEL = {"104": "104 人力銀行", "cake": "CakeResume", "linkedin": "LinkedIn", "yourator": "Yourator",
              "greenhouse": "Greenhouse", "lever": "Lever", "company": "公司官網", "demo": "範例資料"}
FILTERS = {"foreign": "j.is_foreign", "english": "j.work_language = 'en'", "remote": "j.work_mode = 'remote'",
           "hybrid": "j.work_mode = 'hybrid'"}


def job_out(r, detail: bool = False) -> dict:
    r = dict(r)
    d = {"id": str(r["id"]), "source": r["source"], "company_name": r["company_name"],
         "logo_text": (r["company_name"] or "?")[:1].upper(), "title": r["title"],
         "category": {"code": r["category_code"], "label": r["category_label"]} if r.get("category_code") else None,
         "seniority": r["seniority"], "city": r["city"], "location_text": r["location_text"],
         "employment_type": r["employment_type"], "work_mode": r["work_mode"], "work_mode_note": r["work_mode_note"],
         "is_foreign": r["is_foreign"], "work_language": r["work_language"], "salary_text": r["salary_text"],
         "tags": list(r["tags"] or []), "source_site": r["source_site"],
         "source_site_label": SITE_LABEL.get(r["source_site"] or "", r["source_site"]),
         "source_url": r["source_url"], "last_verified_at": r["last_verified_at"], "posted_at": r["posted_at"],
         "parse_status": r["parse_status"], "updated_at": r["updated_at"]}
    if "match_score" in r:
        d["match_score"] = r["match_score"]
    if "target_id" in r:
        d["target_id"] = str(r["target_id"]) if r["target_id"] else None
    if detail:
        d.update({"about": r["about"], "summary": r["summary"], "duties": r["duties"] or [],
                  "requirements": r["requirements"] or []})
    return d


_SELECT = """
SELECT j.*, c.code AS category_code, c.label AS category_label,
       m.score AS match_score, t.id AS target_id
  FROM job_posts j
  LEFT JOIN job_categories c ON c.id = j.category_id
  LEFT JOIN job_matches m ON m.job_post_id = j.id AND m.resume_id = $1
  LEFT JOIN target_jobs t ON t.job_post_id = j.id AND t.user_id = $2 AND t.status = 'active'
"""


async def search(user_id: UUID, *, q: str | None, city: str | None, category: str | None, seniority: str | None,
                 filters: list[str], sort: str, limit: int, offset: int) -> dict:
    async with db.connection() as conn:
        rid = await conn.fetchval("SELECT id FROM resumes WHERE user_id = $1 AND is_primary AND deleted_at IS NULL", user_id)
        where = ["j.source = 'catalog'", "j.status = 'active'", "j.review_status = 'published'", "j.deleted_at IS NULL"]
        args: list = [rid, user_id]

        def arg(v) -> str:
            args.append(v)
            return f"${len(args)}"
        if q:
            where.append(f"(j.search_text ILIKE {arg('%' + q + '%')} OR j.search_text % {arg(q)})")
        if city:
            where.append(f"j.city = {arg(city)}")
        if category:
            where.append(f"c.code = {arg(category)}")
        if seniority:
            where.append(f"j.seniority = {arg(seniority)}")
        for f in filters:
            if f in FILTERS:
                where.append(FILTERS[f])
            elif f == "target":
                where.append("t.id IS NOT NULL")
            elif f == "match80":
                where.append("m.score >= 80")
        order = "j.posted_at DESC NULLS LAST" if sort == "recent" else "m.score DESC NULLS LAST, j.posted_at DESC NULLS LAST"
        sql = f"{_SELECT} WHERE {' AND '.join(where)}"
        total = await conn.fetchval(f"SELECT count(*) FROM ({sql}) x", *args)
        rs = await conn.fetch(f"{sql} ORDER BY {order} LIMIT {arg(limit)} OFFSET {arg(offset)}", *args)
    nxt = str(offset + limit) if offset + limit < total else None
    return {"total": total, "items": [job_out(r) for r in rs], "next_cursor": nxt}


async def get(user_id: UUID, job_id: UUID) -> dict:
    async with db.connection() as conn:
        rid = await conn.fetchval("SELECT id FROM resumes WHERE user_id = $1 AND is_primary AND deleted_at IS NULL", user_id)
        r = await conn.fetchrow(
            f"""{_SELECT} WHERE j.id = $3 AND j.deleted_at IS NULL
                 AND ((j.source = 'catalog' AND j.review_status = 'published') OR j.owner_user_id = $2)""",
            rid, user_id, job_id)
    if not r:
        raise not_found("找不到這個職缺")
    return job_out(r, detail=True)


def search_text(company: str, title: str, tags: list[str]) -> str:
    return " ".join([title or "", company or "", " ".join(tags or [])]).strip()


async def handle_parse_job(payload: dict) -> None:
    """使用者貼上的職缺 → 結構化欄位 → 排出題與面試建議（replace：使用者按了重新出題，換掉 Ava 出的舊題目）。"""
    jid, target_id = UUID(payload["job_post_id"]), UUID(payload["target_id"])
    async with db.connection() as conn:
        j = await conn.fetchrow("UPDATE job_posts SET parse_status = 'parsing' WHERE id = $1 RETURNING *", jid)
    if not j:
        return
    try:
        out = await parsing.parse_jd(j["raw_text"] or "", user_id=j["owner_user_id"])
    except Exception:
        out = parsing.rough_jd(j["raw_text"] or "")    # AI 失敗：改用規則粗解析，出題照常進行
    emb = await ai.embed(f"{out.title}\n{out.company_name}\n" + "\n".join(out.duties + out.requirements),
                         user_id=j["owner_user_id"])
    async with tx() as conn:
        cat = await conn.fetchval("SELECT id FROM job_categories WHERE code = $1", out.category_code)
        await conn.execute(
            """
            UPDATE job_posts SET company_name = $2, title = $3, city = NULLIF($4,''), location_text = NULLIF($5,''),
                   employment_type = NULLIF($6,''), work_mode = NULLIF($7,''), salary_text = NULLIF($8,''),
                   about = NULLIF($9,''), duties = $10, requirements = $11, tags = $12, work_language = $13,
                   seniority = NULLIF($14,''), category_id = $15, search_text = $16, embedding = $17,
                   parse_status = 'parsed'
             WHERE id = $1
            """, jid, out.company_name[:120] or "自訂職缺", out.title[:120] or "未命名職缺", out.city, out.location_text,
            out.employment_type, out.work_mode if out.work_mode in ("onsite", "hybrid", "remote") else "",
            out.salary_text, out.about, out.duties[:10], out.requirements[:10], out.tags[:8],
            out.work_language if out.work_language in ("zh", "en") else "zh",
            out.seniority if out.seniority in ("junior", "mid", "senior", "lead") else "",
            cat, search_text(out.company_name, out.title, out.tags), emb)
        from app.modules.targets import service as targets
        await targets.after_job_ready(conn, target_id, replace=bool(payload.get("replace")))


def days_ago(ts: dt.datetime | None) -> str | None:
    if not ts:
        return None
    d = (dt.datetime.now(dt.timezone.utc) - ts).days
    return "今天" if d <= 0 else f"{d} 天前"
