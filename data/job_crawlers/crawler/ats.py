"""招募系統（ATS）的公開職缺 API：Greenhouse、Lever、Ashby。

這三家都提供官方文件化、給程式讀取的公開 JSON API（不需要登入），一家公司一個請求就拿到全部職缺：
  Greenhouse  https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true
  Lever       https://api.lever.co/v0/postings/{board}?mode=json
  Ashby       https://api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true

每筆職缺轉成 schema.org JobPosting 的形狀，再交給 normalize（和官網爬蟲同一套分類、標籤、審核規則）。
只保留地點在台灣的職缺；外商的全球職缺大多不在台灣，不送進待審清單。
"""
import datetime as dt
import html
import json
import re

from .http import BlockedError, Fetcher
from .normalize import CATEGORY_RULES, _city, _match, normalize

API = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true",
    "lever": "https://api.lever.co/v0/postings/{board}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true",
}
TW = re.compile(r"taiwan|\btw\b|台灣|臺灣", re.I)
# Lever／Ashby 的雇用型態 → JobPosting employmentType
EMPLOYMENT = {"full-time": "FULL_TIME", "fulltime": "FULL_TIME", "part-time": "PART_TIME", "parttime": "PART_TIME",
              "contract": "CONTRACTOR", "contractor": "CONTRACTOR", "intern": "INTERN", "internship": "INTERN",
              "temporary": "TEMPORARY", "temp": "TEMPORARY"}
LEVER_INTERVAL = {"per-year-salary": "YEAR", "per-month-salary": "MONTH", "per-hour-wage": "HOUR", "per-day-wage": "DAY",
                  "per-week-salary": "WEEK"}


def in_taiwan(*texts: str) -> bool:
    blob = " ".join(t for t in texts if t)
    return bool(TW.search(blob) or _city(blob))


def _employment(v: str | None) -> str | None:
    return EMPLOYMENT.get(re.sub(r"\s+", "", (v or "").lower()))


def _posting(src: dict, *, ident: str, title: str, url: str, location: str, description: str, posted,
             department: str = "", employment: str | None = None, remote: bool = False, salary: dict | None = None) -> dict:
    job = {"@type": "JobPosting", "title": title, "url": url, "description": description, "datePosted": posted,
           "_department": department,         # 不是 JobPosting 欄位：給 classify 用
           "identifier": f"{src['board']}:{ident}", "hiringOrganization": {"name": src.get("company_name") or src["board"]},
           # 進到這裡的都已經確認在台灣
           "jobLocation": [{"address": {"addressLocality": location, "addressCountry": "TW"}}]}
    if employment:
        job["employmentType"] = employment
    if remote:
        job["jobLocationType"] = "TELECOMMUTE"
    if salary:
        job["baseSalary"] = salary
    return job


# ---------- 各家格式 ----------
def greenhouse(src: dict, data: dict) -> list[dict]:
    out = []
    for j in data.get("jobs", []):
        loc = (j.get("location") or {}).get("name") or ""
        offices = [o.get("location") or o.get("name") or "" for o in j.get("offices") or []]
        # 以職缺的地點為準；辦公室清單常列出公司全部據點（Canonical 每個職缺都掛全球辦公室），
        # 只有地點欄位沒寫地理位置（例如只寫公司名）而且辦公室全在台灣時才採用
        if not (in_taiwan(loc) or (offices and all(in_taiwan(o) for o in offices))):
            continue
        out.append(_posting(src, ident=str(j["id"]), title=j.get("title") or "", url=j.get("absolute_url") or "",
                            location=loc if in_taiwan(loc) else ", ".join(offices), description=html.unescape(j.get("content") or ""),   # content 是跳脫過的 HTML
                            posted=j.get("first_published") or j.get("updated_at"),
                            department=" ".join(d.get("name") or "" for d in j.get("departments") or [])))
    return out


def lever(src: dict, data: list) -> list[dict]:
    out = []
    for j in data if isinstance(data, list) else []:
        cat = j.get("categories") or {}
        locs = [cat.get("location") or "", *(cat.get("allLocations") or [])]
        if not (j.get("country") == "TW" or in_taiwan(*locs)):
            continue
        lists = "\n".join(f"{x.get('text', '')}\n{x.get('content', '')}" for x in j.get("lists") or [])
        desc = "\n".join(p for p in (j.get("description") or j.get("descriptionPlain"), lists, j.get("additional")) if p)
        sal = j.get("salaryRange") or None
        salary = {"currency": sal.get("currency"), "value": {"minValue": sal.get("min"), "maxValue": sal.get("max"),
                                                              "unitText": LEVER_INTERVAL.get(sal.get("interval"), "")}} if sal else None
        created = j.get("createdAt")
        posted = dt.datetime.fromtimestamp(created / 1000, dt.timezone.utc).isoformat(timespec="seconds") if created else None
        out.append(_posting(src, ident=j["id"], title=j.get("text") or "", url=j.get("hostedUrl") or "",
                            location=", ".join(dict.fromkeys(x for x in locs if x)), description=desc, posted=posted,
                            department=f"{cat.get('department') or ''} {cat.get('team') or ''}",
                            employment=_employment(cat.get("commitment")), remote=j.get("workplaceType") == "remote",
                            salary=salary))
    return out


def ashby(src: dict, data: dict) -> list[dict]:
    out = []
    for j in data.get("jobs", []):
        if j.get("isListed") is False:
            continue
        addr = ((j.get("address") or {}).get("postalAddress") or {})
        locs = [j.get("location") or "", addr.get("addressCountry") or "", addr.get("addressLocality") or "",
                *[s.get("location") or "" for s in j.get("secondaryLocations") or []]]
        if not in_taiwan(*locs):
            continue
        out.append(_posting(src, ident=j["id"], title=j.get("title") or "", url=j.get("jobUrl") or "",
                            location=", ".join(dict.fromkeys(x for x in locs if x)),
                            description=j.get("descriptionHtml") or j.get("descriptionPlain") or "",
                            posted=j.get("publishedAt"), employment=_employment(j.get("employmentType")),
                            department=f"{j.get('department') or ''} {j.get('team') or ''}",
                            remote=bool(j.get("isRemote"))))
    return out


PARSERS = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby}


def classify(n: dict, department: str) -> dict:
    """分類只看職稱和部門：ATS 的描述開頭常是公司簡介（例如「用 AI 的 SaaS 公司」），會把業務職缺誤判成機器學習。
    部門資料可靠，對不上技術分類的（業務、行政⋯）歸「其他」照樣上架，不送待審。"""
    n["category_code"] = _match(CATEGORY_RULES, n["title"]) or _match(CATEGORY_RULES, department) or "other"
    reasons = [r for r in n["review_reasons"] if r != "分類不確定"]
    n["review_reasons"], n["review_status"] = reasons, "published" if not reasons else "pending_review"
    n["ai_confidence"] = round(max(0.3, 0.9 - 0.2 * len(reasons)), 2)
    return n


def crawl_board(fetcher: Fetcher, src: dict) -> tuple[list[dict], list[str], bool]:
    """回傳（正規化後的台灣職缺, 問題紀錄, 這次是否完整讀到職缺板）。完整讀到才能據此下架消失的職缺。"""
    kind = src.get("ats")
    if kind not in API:
        return [], [f"不支援的招募系統：{kind}"], False
    url = API[kind].format(board=src["board"])
    try:
        r = fetcher.get_api(url)
    except BlockedError as e:
        return [], [str(e)], False
    if r.status == 404:
        return [], [f"找不到這個職缺板（{kind}／{src['board']}），代號可能改了"], False
    if r.status >= 400:
        return [], [f"職缺 API 回應 {r.status}：{url}"], False
    try:
        data = json.loads(r.text)
    except ValueError:
        return [], [f"職缺 API 回傳的不是 JSON：{url}"], False
    jobs = []
    for posting in PARSERS[kind](src, data):
        n = normalize(posting, posting["url"])
        if n:
            classify(n, posting["_department"])
            n["source_site"] = kind
            n["is_foreign"] = bool(src.get("foreign"))
            jobs.append(n)
    return jobs, [] if jobs else ["這家公司目前沒有台灣的職缺"], True
