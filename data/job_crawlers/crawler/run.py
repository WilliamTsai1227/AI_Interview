"""爬蟲主流程。

crawl：每家公司 → 徵才頁＋sitemap 找職缺頁 → 讀 JobPosting → 正規化 → （選用）AI 摘要 → 寫入 job_posts
verify：檢查已上架職缺的原始網址：404／410 或已過截止日 → 下架；超過 30 天沒再抓到 → 下架

本機：python -m crawler.run crawl [--sources sources/companies.json] [--limit 3]
"""
import argparse
import datetime as dt
import json
import logging
import sys
import time
from pathlib import Path

from . import db
from .config import Config
from .discover import candidate_links, sitemap_urls
from .http import BlockedError, Fetcher
from .jsonld import parse_html
from .normalize import normalize
from .summarize import summarize

log = logging.getLogger("job_crawler")
DEFAULT_SOURCES = Path(__file__).resolve().parents[1] / "sources" / "companies.json"


def load_sources(path: str | None = None, inline: list | None = None) -> list[dict]:
    if inline:
        return inline
    data = json.loads(Path(path or DEFAULT_SOURCES).read_text(encoding="utf-8"))
    return [s for s in data.get("companies", []) if s.get("enabled", True)]


def crawl_company(fetcher: Fetcher, cfg: Config, src: dict) -> tuple[list[dict], list[str]]:
    """回傳（正規化後的職缺, 問題紀錄）。"""
    issues: list[str] = []
    found: dict[str, dict] = {}
    start = src["careers_url"]

    def take(html_text: str, page_url: str) -> list[str]:
        jobs, links = parse_html(html_text)
        for j in jobs:
            n = normalize(j, page_url)
            if n and n["source_url_norm"] not in found:
                if src.get("company_name") and not n["company_name"]:
                    n["company_name"] = src["company_name"]
                found[n["source_url_norm"]] = n
        return links

    try:
        r = fetcher.get(start)
        if r.status >= 400:
            return [], [f"徵才頁回應 {r.status}：{start}"]
        links = take(r.text, r.url)
    except BlockedError as e:
        return [], [str(e)]
    queue = candidate_links(r.url, links, cfg.max_pages_per_site)
    queue += [u for u in sitemap_urls(fetcher, start, cfg.max_pages_per_site, src.get("sitemaps")) if u not in queue]
    for url in queue:
        if len(found) >= cfg.max_jobs_per_site:
            break
        try:
            r = fetcher.get(url)
        except BlockedError as e:
            issues.append(str(e))
            if "機器人驗證" in str(e) or "上限" in str(e) or "429" in str(e):
                break
            continue
        if r.status < 400 and "html" in (r.content_type or "html"):
            more = take(r.text, r.url)
            # 列表頁（例如 /careers/engineering）再往下一層找職缺頁
            for u in candidate_links(r.url, more, cfg.max_pages_per_site):
                if u not in queue and len(queue) < cfg.max_pages_per_site:
                    queue.append(u)
    if not found:
        issues.append("沒有找到 schema.org JobPosting 結構化資料")
    return list(found.values()), issues


def crawl(cfg: Config, sources: list[dict], deadline: float | None = None) -> dict:
    conn = db.connect(cfg.database_url)
    run_id = db.start_run(conn, {"mode": "crawl", "source": "company_jsonld", "companies": [s["name"] for s in sources]})
    stats = {"mode": "crawl", "fetched": 0, "valid": 0, "published": 0, "review": 0, "closed": 0, "inserted": 0, "updated": 0,
             "summarized": 0, "companies": {}}
    error = None
    try:
        cats = db.category_ids(conn)
        fetcher = Fetcher(cfg)
        for src in sources:
            if deadline and time.monotonic() > deadline:
                stats["companies"][src["name"]] = {"skipped": "時間不夠，下次再抓"}
                continue
            jobs, issues = crawl_company(fetcher, cfg, src)
            stats["fetched"] += len(jobs)
            per = {"jobs": len(jobs), "inserted": 0, "updated": 0, "issues": issues[:10]}
            for j in jobs:
                row = db.existing(conn, j["source_url_norm"], j["external_ref"])
                summ = None
                if not (row and row[2]):          # 新職缺或還沒有摘要才花錢做摘要
                    summ = summarize(cfg, j["title"], j["company_name"], j.get("description", ""))
                    stats["summarized"] += bool(summ)
                result = db.upsert(conn, j, cats, run_id, summ)
                stats[result] += 1
                per[result] += 1
                stats["valid"] += 1
                if result == "inserted":
                    stats["published" if j["review_status"] == "published" else "review"] += 1
            stats["companies"][src["name"]] = per
            log.info("company %s: %s", src["name"], per)
    except Exception as e:      # 記錄失敗原因，不吞掉已寫入的資料
        error = repr(e)
        log.exception("crawl failed")
    finally:
        db.finish_run(conn, run_id, stats, error)
        conn.close()
    stats["run_id"] = str(run_id)
    return stats


def verify(cfg: Config, limit: int = 200, deadline: float | None = None) -> dict:
    conn = db.connect(cfg.database_url)
    run_id = db.start_run(conn, {"mode": "verify"})
    stats = {"checked": 0, "closed": 0, "kept": 0, "skipped": 0}
    try:
        fetcher = Fetcher(cfg)
        now = dt.datetime.now(dt.timezone.utc)
        for job_id, url in db.jobs_to_verify(conn, limit):
            if deadline and time.monotonic() > deadline:
                break
            stats["checked"] += 1
            try:
                r = fetcher.get(url)
            except BlockedError:
                stats["skipped"] += 1
                continue
            if r.status in (404, 410):
                db.close_job(conn, job_id)
                stats["closed"] += 1
                continue
            jobs, _ = parse_html(r.text) if r.status < 400 else ([], [])
            n = normalize(jobs[0], url) if jobs else None
            if n and n["valid_through"] and n["valid_through"] < now:
                db.close_job(conn, job_id)
                stats["closed"] += 1
            else:
                db.mark_verified(conn, job_id)
                stats["kept"] += 1
        stats["closed"] += db.close_stale(conn, 30)
    finally:
        db.finish_run(conn, run_id, {"mode": "verify", "fetched": stats["checked"], "valid": stats["kept"],
                                     "closed": stats["closed"], "skipped": stats["skipped"]})
        conn.close()
    return stats


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="公司官網 JobPosting 職缺爬蟲")
    ap.add_argument("mode", choices=["crawl", "verify"])
    ap.add_argument("--sources", help="公司清單 JSON（預設 sources/companies.json）")
    ap.add_argument("--limit", type=int, help="只抓前 N 家公司")
    a = ap.parse_args(argv)
    cfg = Config()
    if a.mode == "crawl":
        srcs = load_sources(a.sources)
        out = crawl(cfg, srcs[: a.limit] if a.limit else srcs)
    else:
        out = verify(cfg)
    json.dump(out, sys.stdout, ensure_ascii=False, indent=2, default=str)
    print()


if __name__ == "__main__":
    main()
