"""管理後台：匯入平台職缺（合作夥伴／ATS 公開 API）、待審清單、審核、ETL 執行紀錄（api.md §13）。"""
import datetime as dt
import re
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, HttpUrl

from app.core import db
from app.core.errors import not_found
from app.core.security import CurrentUser, admin_user
from app.modules.jobs.service import search_text

router = APIRouter(prefix="/admin", tags=["admin"])


def norm_url(u: str) -> str:
    p = urlsplit(u)
    q = "&".join(x for x in p.query.split("&") if x and not re.match(r"(utm_|ref=|source=|fbclid|gclid)", x))
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/"), q, ""))


class ImportJob(BaseModel):
    company_name: str = Field(max_length=120)
    title: str = Field(max_length=120)
    location_text: str | None = None
    city: str | None = None
    summary: str | None = Field(default=None, max_length=600)
    duties: list[str] = Field(default_factory=list)
    requirements: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    source_site: str = Field(max_length=40)
    source_url: HttpUrl
    external_ref: str | None = Field(default=None, max_length=200)
    posted_at: dt.datetime | None = None
    work_mode: Literal["onsite", "hybrid", "remote"] | None = None
    employment_type: str | None = None
    salary_text: str | None = None
    is_foreign: bool = False
    work_language: Literal["zh", "en"] | None = None
    category_code: str | None = None
    seniority: Literal["junior", "mid", "senior", "lead"] | None = None


class ReviewIn(BaseModel):
    review_status: Literal["published", "rejected", "closed"]
    category_code: str | None = None
    seniority: Literal["junior", "mid", "senior", "lead"] | None = None
    note: str | None = None


@router.post("/jobs/import")
async def import_jobs(items: list[ImportJob], _: CurrentUser = Depends(admin_user)):
    inserted = updated = 0
    async with db.transaction() as conn:
        for j in items:
            cat = await conn.fetchval("SELECT id FROM job_categories WHERE code = $1", j.category_code) if j.category_code else None
            url = str(j.source_url)
            ref = j.external_ref or norm_url(url)
            r = await conn.fetchrow(
                """
                INSERT INTO job_posts (source, external_ref, company_name, title, city, location_text, summary, duties, requirements,
                       tags, source_site, source_url, source_url_norm, posted_at, work_mode, employment_type, salary_text,
                       is_foreign, work_language, category_id, seniority, fetched_at, last_verified_at, search_text, review_status)
                VALUES ('catalog',$1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20, now(), now(), $21, 'published')
                ON CONFLICT (source_site, external_ref) WHERE external_ref IS NOT NULL DO UPDATE SET
                       company_name = EXCLUDED.company_name, title = EXCLUDED.title, city = EXCLUDED.city,
                       location_text = EXCLUDED.location_text, summary = EXCLUDED.summary, duties = EXCLUDED.duties,
                       requirements = EXCLUDED.requirements, tags = EXCLUDED.tags, source_url = EXCLUDED.source_url,
                       work_mode = EXCLUDED.work_mode, salary_text = EXCLUDED.salary_text, category_id = EXCLUDED.category_id,
                       seniority = EXCLUDED.seniority, last_verified_at = now(), search_text = EXCLUDED.search_text, status = 'active'
                RETURNING (xmax = 0) AS inserted
                """, ref, j.company_name, j.title, j.city, j.location_text, j.summary, j.duties, j.requirements, j.tags,
                j.source_site, url, norm_url(url), j.posted_at, j.work_mode, j.employment_type, j.salary_text, j.is_foreign,
                j.work_language, cat, j.seniority, search_text(j.company_name, j.title, j.tags))
            inserted += r["inserted"]
            updated += not r["inserted"]
    return {"inserted": inserted, "updated": updated}


@router.get("/jobs/review-queue")
async def review_queue(_: CurrentUser = Depends(admin_user)):
    async with db.connection() as conn:
        rs = await conn.fetch(
            """SELECT j.id, j.title, j.company_name, j.source_url, c.code, j.ai_confidence, j.review_reasons, j.fetched_at
                 FROM job_posts j LEFT JOIN job_categories c ON c.id = j.category_id
                WHERE j.review_status = 'pending_review' ORDER BY j.fetched_at LIMIT 100""")
    return {"items": [{"id": str(r["id"]), "title": r["title"], "company_name": r["company_name"], "source_url": r["source_url"],
                       "category": {"code": r["code"]}, "ai_confidence": float(r["ai_confidence"]) if r["ai_confidence"] is not None else None,
                       "review_reasons": r["review_reasons"], "fetched_at": r["fetched_at"]} for r in rs], "next_cursor": None}


@router.patch("/jobs/{job_id}/review")
async def review(job_id: str, body: ReviewIn, _: CurrentUser = Depends(admin_user)):
    async with db.transaction() as conn:
        cat = await conn.fetchval("SELECT id FROM job_categories WHERE code = $1", body.category_code) if body.category_code else None
        status = "closed" if body.review_status == "closed" else "active"
        rs = "published" if body.review_status == "closed" else body.review_status
        r = await conn.fetchval(
            """UPDATE job_posts SET review_status = $2, status = $3, category_id = COALESCE($4, category_id),
                      seniority = COALESCE($5, seniority) WHERE id = $1::uuid AND source = 'catalog' RETURNING id""",
            job_id, rs, status, cat, body.seniority)
    if not r:
        raise not_found()
    return {"id": job_id, "review_status": rs, "status": status}


@router.get("/job-ingest-runs")
async def ingest_runs(_: CurrentUser = Depends(admin_user)):
    async with db.connection() as conn:
        rs = await conn.fetch("SELECT * FROM job_ingest_runs ORDER BY started_at DESC LIMIT 50")
    return {"items": [dict(r) | {"id": str(r["id"])} for r in rs]}
