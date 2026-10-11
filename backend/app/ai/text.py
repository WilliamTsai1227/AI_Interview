"""文字工具：去重用的三元組相似度（中英混合）、贅詞與語速計算、職缺文字粗解析。"""
import re

CATEGORIES = ["自我介紹", "技術深度", "系統設計", "團隊合作", "情境題", "動機與規劃"]
DIFFICULTIES = ["basic", "medium", "advanced"]
DIFF_LABEL = {"basic": "基礎", "medium": "中等", "advanced": "進階"}

TECH = ["Python", "Java", "Go", "Golang", "Rust", "C++", "C#", "Node.js", "TypeScript", "JavaScript", "React",
        "Vue", "Angular", "Next.js", "Svelte", "React Native", "Flutter", "Swift", "Kotlin", "Django", "FastAPI",
        "Flask", "Spring", "Rails", "PostgreSQL", "MySQL", "MongoDB", "Redis", "Kafka", "RabbitMQ", "Elasticsearch",
        "Docker", "Kubernetes", "AWS", "GCP", "Azure", "Terraform", "GraphQL", "gRPC", "CI/CD", "Linux",
        "Machine Learning", "PyTorch", "TensorFlow", "LLM", "Spark", "Airflow", "dbt", "SQL", "Figma", "CSS",
        "HTML", "Tailwind", "Jest", "Playwright", "Cypress", "微服務", "設計系統", "資料視覺化", "SEO", "i18n"]
CITIES = ["台北市", "新北市", "桃園市", "新竹市", "新竹縣", "台中市", "台南市", "高雄市", "基隆市", "宜蘭縣", "苗栗縣",
          "彰化縣", "嘉義市", "屏東縣", "花蓮縣", "台東縣"]
FILLERS_ZH = ["嗯", "呃", "那個", "就是", "然後", "應該吧", "好像", "對對", "欸"]
FILLERS_EN = ["um", "uh", "like", "you know", "kind of", "sort of", "basically"]


def _grams(s: str) -> set[str]:
    s = re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", s.lower())).strip()
    s = f"  {s} "
    return {s[i:i + 3] for i in range(len(s) - 2)}


def similarity(a: str, b: str) -> float:
    ga, gb = _grams(a), _grams(b)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga | gb)


def is_duplicate(text: str, existing: list[str], threshold: float = 0.6) -> bool:
    return any(similarity(text, e) > threshold for e in existing)


def filler_stats(text: str, language: str) -> tuple[int, dict[str, int]]:
    terms: dict[str, int] = {}
    low = text.lower()
    for w in (FILLERS_EN if language == "en" else FILLERS_ZH + FILLERS_EN):
        n = len(re.findall(rf"\b{re.escape(w)}\b", low)) if w.isascii() else low.count(w)
        if n:
            terms[w] = n
    return sum(terms.values()), terms


def speech_rate(text: str, duration_ms: int | None, language: str) -> float | None:
    if not duration_ms or duration_ms < 3000:
        return None
    minutes = duration_ms / 60000
    if language == "en":
        return round(len(re.findall(r"[A-Za-z']+", text)) / minutes, 1)
    return round(len(re.findall(r"[一-鿿]", text)) / minutes, 1)


def find_tags(text: str, limit: int = 6) -> list[str]:
    found = []
    low = text.lower()
    for t in TECH:
        if (re.search(rf"(?<![a-z]){re.escape(t.lower())}(?![a-z])", low) if t.isascii() else t in text) and t not in found:
            found.append(t)
    return found[:limit]


def find_city(text: str) -> str | None:
    t = text.replace("臺", "台")
    for c in CITIES:
        if c in t:
            return c
    return None


_BULLET = re.compile(r"^\s*(?:[-*•・●▪︎◦]|\d+[.)、．]|[（(]\d+[)）])\s*")
_DUTY = re.compile(r"(工作內容|職責|你會做|你將|負責|Responsibilities|What you.ll do|Job Description)", re.I)
_REQ = re.compile(r"(條件|要求|資格|需求|必備|加分|Requirements|Qualifications|What we.re looking for|Who you are)", re.I)


def rough_parse_jd(raw: str) -> dict:
    """沒有 AI 時的職缺粗解析：第一行「職稱｜公司」、段落標題分出工作內容與條件。"""
    lines = [l.strip() for l in raw.replace("\r", "").split("\n") if l.strip()]
    first = lines[0] if lines else ""
    parts = [p.strip() for p in re.split(r"[｜|@／/–—]| - | at ", first) if p.strip()]
    title = (parts[0] if parts else first)[:80] or "未命名職缺"
    company = (parts[1] if len(parts) > 1 else "")[:60]
    if not company:
        m = re.search(r"(?:公司|Company)[:：]\s*(\S+)", raw)
        company = m.group(1)[:60] if m else "自訂職缺"
    duties, reqs, about, cur = [], [], [], None
    for l in lines[1:]:
        head = l.rstrip("：:")
        if len(head) <= 24 and _DUTY.search(head):
            cur = duties
            rest = re.split(r"[：:]", l, 1)
            if len(rest) > 1 and rest[1].strip():
                duties.append(rest[1].strip())
            continue
        if len(head) <= 24 and _REQ.search(head):
            cur = reqs
            rest = re.split(r"[：:]", l, 1)
            if len(rest) > 1 and rest[1].strip():
                reqs.append(rest[1].strip())
            continue
        item = _BULLET.sub("", l)
        if cur is not None:
            cur.append(item[:160])
        else:
            about.append(item)
    if not duties and not reqs:
        bullets = [_BULLET.sub("", l) for l in lines[1:] if _BULLET.match(l)]
        duties = bullets[: len(bullets) // 2 or len(bullets)]
        reqs = bullets[len(duties):]
    return {"title": title, "company_name": company, "about": " ".join(about)[:400] or None,
            "duties": duties[:8], "requirements": reqs[:8], "tags": find_tags(raw), "city": find_city(raw),
            "location_text": find_city(raw)}
