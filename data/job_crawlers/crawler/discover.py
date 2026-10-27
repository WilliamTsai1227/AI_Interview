"""找出一家公司的職缺頁網址：徵才頁上的連結＋robots.txt 宣告的 sitemap。只找同一個網域。"""
import re
import xml.etree.ElementTree as ET
from urllib.parse import urldefrag, urljoin, urlsplit

from .http import BlockedError, Fetcher

JOB_PATH = re.compile(r"/(jobs?|careers?|positions?|openings?|vacanc(y|ies)|opportunit(y|ies)|join-us|recruit|職缺|徵才)(/|$|\?|-)", re.I)
SKIP_EXT = re.compile(r"\.(pdf|jpe?g|png|gif|svg|webp|zip|docx?|xlsx?|mp4|css|js)(\?|$)", re.I)
NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}


def same_site(a: str, b: str) -> bool:
    ha, hb = urlsplit(a).netloc.lower(), urlsplit(b).netloc.lower()
    return ha == hb or ha.endswith("." + hb) or hb.endswith("." + ha)


def candidate_links(base_url: str, hrefs: list[str], limit: int) -> list[str]:
    seen, out = set(), []
    for href in hrefs:
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        u = urldefrag(urljoin(base_url, href))[0]
        if u in seen or not same_site(u, base_url) or SKIP_EXT.search(u) or not JOB_PATH.search(urlsplit(u).path + "/"):
            continue
        if u.rstrip("/") == base_url.rstrip("/"):
            continue
        seen.add(u)
        out.append(u)
        if len(out) >= limit:
            break
    return out


def sitemap_urls(fetcher: Fetcher, site_url: str, limit: int, extra: list[str] | None = None) -> list[str]:
    """讀 sitemap（含 sitemap index 與 .gz），只留像職缺頁的網址。"""
    queue = list(extra or []) + fetcher.sitemaps(site_url)
    seen, out = set(), []
    while queue and len(out) < limit and len(seen) < 10:
        sm = queue.pop(0)
        if sm in seen or not same_site(sm, site_url):
            continue
        seen.add(sm)
        try:
            r = fetcher.get(sm)
            root = ET.fromstring(r.text.encode("utf-8"))
        except (BlockedError, ET.ParseError, ValueError):
            continue
        tag = root.tag.split("}")[-1]
        for loc in root.findall(".//sm:loc", NS):
            u = (loc.text or "").strip()
            if not u:
                continue
            if tag == "sitemapindex":
                if re.search(r"job|career|position|opening|vacanc", u, re.I):
                    queue.append(u)
            elif same_site(u, site_url) and JOB_PATH.search(urlsplit(u).path + "/"):
                out.append(u)
                if len(out) >= limit:
                    break
    return out
