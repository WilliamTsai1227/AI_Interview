"""JobPosting → job_posts 欄位。只取事實欄位；原文描述只用來推算標籤與分類，不存進資料庫。"""
import datetime as dt
import hashlib
import html
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

CITIES = {
    "台北市": ["台北", "臺北", "taipei"], "新北市": ["新北", "new taipei"], "桃園市": ["桃園", "taoyuan"],
    "新竹市": ["新竹市", "hsinchu city"], "新竹縣": ["新竹縣", "竹北", "hsinchu county", "zhubei"],
    "台中市": ["台中", "臺中", "taichung"], "台南市": ["台南", "臺南", "tainan"], "高雄市": ["高雄", "kaohsiung"],
    "基隆市": ["基隆", "keelung"], "宜蘭縣": ["宜蘭", "yilan"], "苗栗縣": ["苗栗", "miaoli"], "彰化縣": ["彰化", "changhua"],
    "嘉義市": ["嘉義", "chiayi"], "屏東縣": ["屏東", "pingtung"], "花蓮縣": ["花蓮", "hualien"], "台東縣": ["台東", "臺東", "taitung"],
}
EMPLOYMENT = {"FULL_TIME": "全職", "PART_TIME": "兼職", "CONTRACTOR": "約聘", "TEMPORARY": "臨時", "INTERN": "實習",
              "VOLUNTEER": "志工", "PER_DIEM": "按日計酬", "OTHER": "其他"}
SALARY_UNIT = {"MONTH": ("month", "月薪"), "YEAR": ("year", "年薪"), "HOUR": (None, "時薪"), "DAY": (None, "日薪"), "WEEK": (None, "週薪")}
TECH = ["Python", "Java", "Go", "Golang", "Rust", "C++", "C#", "Node.js", "TypeScript", "JavaScript", "React", "Vue",
        "Angular", "Next.js", "React Native", "Flutter", "Swift", "Kotlin", "Django", "FastAPI", "Spring", "PostgreSQL",
        "MySQL", "MongoDB", "Redis", "Kafka", "Elasticsearch", "Docker", "Kubernetes", "AWS", "GCP", "Azure", "Terraform",
        "GraphQL", "gRPC", "Linux", "PyTorch", "TensorFlow", "LLM", "Spark", "Airflow", "SQL", "Figma", "CSS", "iOS", "Android"]
CATEGORY_RULES = [
    ("ml", r"machine learning|\bml\b|ai engineer|\bai\b|llm|data scien|機器學習|人工智慧|演算法"),
    ("data", r"data engineer|data analyst|analytics|\bbi\b|資料工程|數據|資料分析"),
    ("devops", r"devops|\bsre\b|site reliability|infrastructure|platform engineer|cloud engineer|維運|雲端"),
    ("mobile", r"\bios\b|android|mobile|flutter|react native|app 工程|行動"),
    ("frontend", r"front[- ]?end|frontend|\bui engineer\b|web engineer|前端"),
    ("backend", r"back[- ]?end|backend|server|\bapi\b|後端|伺服器"),
    ("fullstack", r"full[- ]?stack|全端"),
    ("qa", r"\bqa\b|quality|test(ing)? engineer|\bsdet\b|測試"),
    ("pm", r"product manager|\bpm\b|product owner|產品經理"),
    ("design", r"designer|\bux\b|\bui\b|設計"),
]
SENIORITY_RULES = [("lead", r"lead|principal|staff|manager|head|director|主管|經理|架構師"),
                   ("senior", r"senior|\bsr\.?\b|資深"), ("junior", r"junior|\bjr\.?\b|intern|entry|graduate|初階|新鮮人|實習")]


def norm_url(u: str) -> str:
    p = urlsplit(u)
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
         if not re.match(r"(utm_|ref$|source$|fbclid|gclid|mc_)", k, re.I)]
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/") or "/", urlencode(q), ""))


def _text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        return " ".join(_text(x) for x in v)
    if isinstance(v, dict):
        return _text(v.get("name") or v.get("value") or "")
    return str(v)


def strip_html(s: str) -> str:
    s = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", s or "", flags=re.S | re.I)
    s = re.sub(r"<br\s*/?>|</p>|</li>|</div>|</h\d>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"[ \t\r\f\v]+", " ", html.unescape(s)).strip()


def _date(v) -> dt.datetime | None:
    s = _text(v).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            d = dt.datetime.strptime(s.replace("Z", "+0000"), fmt)
            return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)
        except ValueError:
            continue
    return None


def _city(text: str) -> str | None:
    low = text.lower().replace("臺", "台")
    for city, keys in CITIES.items():
        if any(k in low for k in keys):
            return city
    return None


def _location(job: dict) -> tuple[str | None, str | None, bool]:
    locs = job.get("jobLocation")
    locs = locs if isinstance(locs, list) else [locs] if locs else []
    parts, countries = [], []
    for loc in locs:
        addr = (loc or {}).get("address") if isinstance(loc, dict) else None
        if isinstance(addr, str):
            parts.append(addr)
        elif isinstance(addr, dict):
            parts.extend(_text(addr.get(k)) for k in ("addressRegion", "addressLocality", "streetAddress"))
            countries.append(_text(addr.get("addressCountry")))
    text = " ".join(p for p in parts if p)
    in_tw = not countries or any(re.search(r"^(tw|twn|taiwan|台灣|臺灣)$", c.strip(), re.I) for c in countries if c) or bool(_city(text))
    loc_text = " ".join(dict.fromkeys(p.strip() for p in parts if p and p.strip()))[:120] or None
    return _city(text), loc_text, in_tw


def _salary(job: dict) -> tuple[str | None, int | None, int | None, str | None]:
    bs = job.get("baseSalary") or job.get("estimatedSalary")
    if isinstance(bs, list):
        bs = bs[0] if bs else None
    if not isinstance(bs, dict):
        return None, None, None, None
    cur = (bs.get("currency") or "").upper()
    val = bs.get("value")
    lo = hi = None
    unit = ""
    if isinstance(val, dict):
        unit = (val.get("unitText") or "").upper()
        lo, hi = val.get("minValue"), val.get("maxValue")
        if lo is None and hi is None:
            lo = hi = val.get("value")
    elif isinstance(val, (int, float, str)):
        lo = hi = val
    try:
        lo = int(float(lo)) if lo not in (None, "") else None
        hi = int(float(hi)) if hi not in (None, "") else None
    except (TypeError, ValueError):
        return None, None, None, None
    if lo is None and hi is None:
        return None, None, None, None
    period, label = SALARY_UNIT.get(unit, (None, "薪資"))
    money = (lambda n: f"{n:,}")
    rng = money(lo) if lo == hi or hi is None else f"{money(lo or 0)} – {money(hi)}"
    prefix = "" if cur in ("", "TWD", "NTD") else f"{cur} "
    return f"{label} {prefix}{rng}", lo, hi, period


def _tags(text: str) -> list[str]:
    out = []
    for t in TECH:
        if re.search(rf"(?<![A-Za-z]){re.escape(t)}(?![A-Za-z])", text, re.I) and t not in out:
            out.append(t)
    return out[:8]


def _match(rules, text: str) -> str | None:
    for code, pat in rules:
        if re.search(pat, text, re.I):
            return code
    return None


def normalize(job: dict, page_url: str) -> dict | None:
    """回傳 job_posts 欄位（＋ description 只給摘要用，不入庫）；必要欄位缺少時回傳 None。"""
    title = strip_html(_text(job.get("title") or job.get("name")))[:120]
    org = job.get("hiringOrganization")
    company = strip_html(_text(org.get("name") if isinstance(org, dict) else org))[:120]
    url = _text(job.get("url")).strip() or page_url
    if not url.startswith("http"):
        url = page_url
    if not title or not company:
        return None
    desc = strip_html(_text(job.get("description")))
    city, loc_text, in_tw = _location(job)
    remote = "TELECOMMUTE" in _text(job.get("jobLocationType")).upper()
    salary_text, smin, smax, speriod = _salary(job)
    et = job.get("employmentType")
    et = et if isinstance(et, list) else [et] if et else []
    employment = "・".join(dict.fromkeys(EMPLOYMENT.get(str(x).upper().replace("-", "_"), str(x)) for x in et if x)) or None
    posted, valid = _date(job.get("datePosted")), _date(job.get("validThrough"))
    blob = f"{title}\n{desc}"
    category = _match(CATEGORY_RULES, title) or _match(CATEGORY_RULES, desc[:500])
    seniority = _match(SENIORITY_RULES, title)
    ascii_ratio = sum(c.isascii() for c in blob) / max(1, len(blob))
    ident = job.get("identifier")
    ext = _text(ident.get("value") if isinstance(ident, dict) else ident).strip()
    nurl = norm_url(url)
    reasons = []
    if not category:
        reasons.append("分類不確定")
    if not in_tw and not remote:
        reasons.append("地點不在台灣")
    if valid and valid < dt.datetime.now(dt.timezone.utc):
        reasons.append("已超過截止日期")
    if not desc:
        reasons.append("沒有職缺描述")
    confidence = round(max(0.3, 0.9 - 0.2 * len(reasons)), 2)
    return {
        "title": title, "company_name": company, "source_url": url, "source_url_norm": nurl,
        "external_ref": ext or hashlib.sha1(nurl.encode()).hexdigest()[:24],
        "city": city, "location_text": loc_text, "work_mode": "remote" if remote else None,
        "employment_type": employment, "salary_text": salary_text, "salary_min": smin, "salary_max": smax,
        "salary_period": speriod, "tags": _tags(blob), "category_code": category or "other", "seniority": seniority,
        "work_language": "en" if ascii_ratio > 0.85 else "zh", "is_foreign": False, "posted_at": posted,
        "valid_through": valid, "ai_confidence": confidence,
        "review_status": "published" if not reasons else "pending_review", "review_reasons": reasons,
        "description": desc[:6000],
    }
