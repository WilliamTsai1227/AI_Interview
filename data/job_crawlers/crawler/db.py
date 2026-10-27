"""寫入 PostgreSQL（與主系統同一個資料庫）：job_posts、job_ingest_runs。

用 pg8000（純 Python，Lambda 打包不需要編譯），一律參數化 SQL。
"""
import json
import os
import ssl
from urllib.parse import unquote, urlsplit

import pg8000.native

SOURCE_SITE = "company"     # 主系統顯示為「公司官網」


def connect(database_url: str) -> pg8000.native.Connection:
    u = urlsplit(database_url)
    kw = {"user": unquote(u.username or ""), "password": unquote(u.password or ""), "host": u.hostname or "localhost",
          "port": u.port or 5432, "database": (u.path or "/").lstrip("/") or "postgres", "timeout": 15}
    if os.environ.get("DATABASE_SSL", "").lower() in ("1", "true", "require"):
        kw["ssl_context"] = ssl.create_default_context()
    return pg8000.native.Connection(**kw)


def start_run(conn, query: dict) -> str:
    return conn.run("INSERT INTO job_ingest_runs (query) VALUES (CAST(:q AS jsonb)) RETURNING id",
                    q=json.dumps(query, ensure_ascii=False))[0][0]


def finish_run(conn, run_id, stats: dict, error: str | None = None) -> None:
    conn.run(
        """UPDATE job_ingest_runs SET status = :st, fetched_count = :f, valid_count = :v, published_count = :p,
                  review_count = :r, closed_count = :c, error = :e, details = CAST(:d AS jsonb), finished_at = now()
            WHERE id = :id""",
        d=json.dumps(stats, ensure_ascii=False, default=str),
        st="failed" if error and not stats.get("valid") else "succeeded", f=stats.get("fetched", 0),
        v=stats.get("valid", 0), p=stats.get("published", 0), r=stats.get("review", 0), c=stats.get("closed", 0),
        e=(error or "")[:1000] or None, id=run_id)


def category_ids(conn) -> dict[str, str]:
    return {code: str(i) for i, code in conn.run("SELECT id, code FROM job_categories")}


def existing(conn, url_norm: str, external_ref: str):
    rows = conn.run(
        """SELECT id, review_status, summary IS NOT NULL FROM job_posts
            WHERE deleted_at IS NULL AND (source_url_norm = :u OR (source_site = :s AND external_ref = :x)) LIMIT 1""",
        u=url_norm, s=SOURCE_SITE, x=external_ref)
    return rows[0] if rows else None


def search_text(company: str, title: str, tags: list[str]) -> str:
    return " ".join([title or "", company or "", " ".join(tags or [])]).strip()


def upsert(conn, j: dict, cat_ids: dict, run_id, summary: dict | None) -> str:
    """新職缺：用推算的審核狀態；已存在：更新事實欄位，保留管理者的審核結果。回傳 inserted / updated。"""
    row = existing(conn, j["source_url_norm"], j["external_ref"])
    params = dict(
        title=j["title"], company=j["company_name"], url=j["source_url"], url_norm=j["source_url_norm"],
        ext=j["external_ref"], city=j["city"], loc=j["location_text"], mode=j["work_mode"], etype=j["employment_type"],
        sal=j["salary_text"], smin=j["salary_min"], smax=j["salary_max"], sper=j["salary_period"], tags=j["tags"],
        cat=cat_ids.get(j["category_code"]), sen=j["seniority"], lang=j["work_language"], posted=j["posted_at"],
        conf=j["ai_confidence"], run=run_id, st=search_text(j["company_name"], j["title"], j["tags"]),
        raw=j.get("description") or None,     # 原文：只給 AI 出題與分析用，主系統 API 不回傳
        summary=(summary or {}).get("summary"), duties=json.dumps((summary or {}).get("duties", []), ensure_ascii=False),
        reqs=json.dumps((summary or {}).get("requirements", []), ensure_ascii=False))
    if row:
        conn.run(
            """UPDATE job_posts SET title = :title, company_name = :company, source_url = :url, city = :city,
                      location_text = :loc, work_mode = COALESCE(:mode, work_mode), employment_type = :etype,
                      salary_text = :sal, salary_min = :smin, salary_max = :smax, salary_period = :sper,
                      tags = CAST(:tags AS text[]), category_id = COALESCE(category_id, CAST(:cat AS uuid)),
                      seniority = COALESCE(seniority, :sen), work_language = :lang, posted_at = COALESCE(:posted, posted_at),
                      ai_confidence = :conf, ingest_run_id = :run, search_text = :st, fetched_at = now(),
                      last_verified_at = now(), status = 'active', raw_text = COALESCE(:raw, raw_text),
                      summary = COALESCE(CAST(:summary AS text), summary),
                      duties = CASE WHEN CAST(:summary AS text) IS NULL THEN duties ELSE CAST(:duties AS jsonb) END,
                      requirements = CASE WHEN CAST(:summary AS text) IS NULL THEN requirements ELSE CAST(:reqs AS jsonb) END
                WHERE id = :id""", id=row[0], **{k: v for k, v in params.items() if k not in ("url_norm", "ext")})
        return "updated"
    conn.run(
        """INSERT INTO job_posts (source, source_site, external_ref, company_name, title, source_url, source_url_norm,
                  city, location_text, work_mode, employment_type, salary_text, salary_min, salary_max, salary_period,
                  tags, category_id, seniority, work_language, posted_at, ai_confidence, review_status, review_reasons,
                  ingest_run_id, search_text, fetched_at, last_verified_at, summary, duties, requirements, raw_text)
           VALUES ('catalog', :site, :ext, :company, :title, :url, :url_norm, :city, :loc, :mode, :etype, :sal, :smin,
                   :smax, :sper, CAST(:tags AS text[]), CAST(:cat AS uuid), :sen, :lang, :posted, :conf, :review,
                   CAST(:reasons AS jsonb), :run, :st, now(), now(), :summary, CAST(:duties AS jsonb), CAST(:reqs AS jsonb), :raw)""",
        site=SOURCE_SITE, review=j["review_status"], reasons=json.dumps(j["review_reasons"], ensure_ascii=False), **params)
    return "inserted"


def jobs_to_verify(conn, limit: int):
    return conn.run(
        """SELECT id, source_url FROM job_posts
            WHERE source = 'catalog' AND source_site = :s AND status = 'active' AND deleted_at IS NULL
              AND (last_verified_at IS NULL OR last_verified_at < now() - interval '20 hours')
            ORDER BY last_verified_at NULLS FIRST LIMIT :n""", s=SOURCE_SITE, n=limit)


def mark_verified(conn, job_id) -> None:
    conn.run("UPDATE job_posts SET last_verified_at = now() WHERE id = :id", id=job_id)


def close_job(conn, job_id) -> None:
    conn.run("UPDATE job_posts SET status = 'closed', last_verified_at = now() WHERE id = :id", id=job_id)


def close_stale(conn, days: int = 30) -> int:
    """超過 N 天沒有再被抓到的職缺下架。"""
    rows = conn.run(
        """UPDATE job_posts SET status = 'closed' WHERE source = 'catalog' AND source_site = :s AND status = 'active'
              AND fetched_at < now() - make_interval(days => :d) RETURNING id""", s=SOURCE_SITE, d=days)
    return len(rows)
