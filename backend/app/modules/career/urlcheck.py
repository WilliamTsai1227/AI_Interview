"""檢查網路搜尋回來的職缺網址是否真的存在。

結果：
- ok          ：能打開（2xx／3xx 最後落在詳情頁）
- unverified  ：網站阻擋自動檢查（401／403／429／503，例如 104、CakeResume 的 Cloudflare 驗證），
                但網址是該網站「單一職缺詳情頁」的格式 → 保留，前端標示「無法自動確認」
- 其他        ：404／410、連不上、網址不是詳情頁、被導回首頁或列表頁 → 丟掉（回傳 None）
只送一個請求看狀態碼，不抓取內容。
"""
import asyncio
import re
from urllib.parse import urlsplit

import httpx

UA = "Mozilla/5.0 (compatible; AIInterviewLinkCheck/0.1; +https://example.com/bot)"
DETAIL_PATTERNS = {
    "104.com.tw": re.compile(r"^/job/[A-Za-z0-9]+/?$"),
    "cake.me": re.compile(r"^/(companies/[^/]+/jobs/[^/]+|jobs/[^/]+)/?$"),
    "cakeresume.com": re.compile(r"^/(companies/[^/]+/jobs/[^/]+|jobs/[^/]+)/?$"),
    "yourator.co": re.compile(r"^/companies/[^/]+/jobs/\d+/?$"),
    "linkedin.com": re.compile(r"^/jobs/view/[^/]+/?$"),
}
LISTING_PATH = re.compile(r"^/?$|/(search|jobs/?$|list|category)", re.I)


def _site(host: str) -> str | None:
    host = host.lower()
    return next((d for d in DETAIL_PATTERNS if host == d or host.endswith("." + d)), None)


def looks_like_detail(url: str) -> bool:
    u = urlsplit(url)
    site = _site(u.netloc)
    if site:
        return bool(DETAIL_PATTERNS[site].match(u.path))
    return not LISTING_PATH.search(u.path)        # 公司官網：至少不是首頁或搜尋頁


async def _check(client: httpx.AsyncClient, url: str) -> str | None:
    if not url.startswith(("http://", "https://")) or not looks_like_detail(url):
        return None
    try:
        r = await client.get(url, follow_redirects=True)
    except (httpx.HTTPError, ValueError):
        return None
    if r.status_code in (404, 410) or r.status_code >= 500 and r.status_code != 503:
        return None
    if 200 <= r.status_code < 400:
        final = str(r.url)
        # 職缺下架時常被導回首頁或列表頁
        return "ok" if looks_like_detail(final) else None
    if r.status_code in (401, 403, 429, 503):
        return "unverified"
    return None


async def check_urls(urls: list[str], concurrency: int = 5) -> dict[str, str | None]:
    sem = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(timeout=10, headers={"User-Agent": UA}) as client:
        async def one(u: str):
            async with sem:
                return u, await _check(client, u)
        return dict(await asyncio.gather(*(one(u) for u in urls)))
