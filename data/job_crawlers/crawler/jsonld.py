"""從 HTML 取出 schema.org JobPosting（<script type="application/ld+json">）。

公司徵才頁放這份資料，是為了讓 Google 職缺搜尋讀取；格式見 https://schema.org/JobPosting
支援：單一物件、陣列、@graph、@type 為陣列。
"""
import json
import re
from html.parser import HTMLParser


class _LdParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks: list[str] = []
        self.links: list[str] = []
        self._in_ld = False
        self._buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "script" and (a.get("type") or "").lower().strip() == "application/ld+json":
            self._in_ld, self._buf = True, []
        elif tag == "a" and a.get("href"):
            self.links.append(a["href"])

    def handle_endtag(self, tag):
        if tag == "script" and self._in_ld:
            self.blocks.append("".join(self._buf))
            self._in_ld = False

    def handle_data(self, data):
        if self._in_ld:
            self._buf.append(data)


def parse_html(html: str) -> tuple[list[dict], list[str]]:
    """回傳（JobPosting 物件清單, 頁面上的連結）。"""
    p = _LdParser()
    try:
        p.feed(html)
    except Exception:
        pass
    jobs: list[dict] = []
    for raw in p.blocks:
        data = _loads(raw)
        if data is not None:
            jobs.extend(_collect(data))
    return jobs, p.links


def _loads(raw: str):
    raw = raw.strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # 常見錯誤：結尾多逗號、控制字元
        cleaned = re.sub(r",\s*([}\]])", r"\1", re.sub(r"[\x00-\x1f]", " ", raw))
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            return None


def _is_job(o: dict) -> bool:
    t = o.get("@type")
    types = t if isinstance(t, list) else [t]
    return any(isinstance(x, str) and x.split("/")[-1] == "JobPosting" for x in types)


def _collect(data) -> list[dict]:
    out: list[dict] = []
    if isinstance(data, list):
        for x in data:
            out.extend(_collect(x))
    elif isinstance(data, dict):
        if _is_job(data):
            out.append(data)
        for key in ("@graph", "itemListElement", "mainEntity"):
            if key in data:
                out.extend(_collect(data[key]))
        if "item" in data and isinstance(data["item"], dict):
            out.extend(_collect(data["item"]))
    return out
