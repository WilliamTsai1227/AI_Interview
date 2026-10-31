"""檢查網路搜尋回來的職缺網址是否真的存在。

結果：
- ok          ：能打開（2xx／3xx 最後落在詳情頁）
- unverified  ：網站阻擋自動檢查（401／403／429／503，例如 104、CakeResume 的 Cloudflare 驗證），
                但網址是該網站「單一職缺詳情頁」的格式 → 保留，前端標示「無法自動確認」
- 其他        ：404／410、連不上、網址不是詳情頁、被導回首頁或列表頁 → 丟掉（回傳 None）
只送一個請求看狀態碼，不抓取內容。

防 SSRF：網址是 AI 回傳的，可能被履歷或自我介紹裡的提示注入操控，指向內部服務
（例如 http://db:5432、http://169.254.169.254 雲端中繼資料）。所以只連 80／443 埠、
DNS 解析後必須全是公開 IP，轉址也自己一跳一跳檢查（最多 5 跳），不交給 httpx 自動跟隨。
"""
import asyncio
import ipaddress
import re
import socket
from urllib.parse import urljoin, urlsplit

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
MAX_REDIRECTS = 5


def _site(host: str) -> str | None:
    host = host.lower()
    return next((d for d in DETAIL_PATTERNS if host == d or host.endswith("." + d)), None)


def looks_like_detail(url: str) -> bool:
    u = urlsplit(url)
    site = _site(u.netloc)
    if site:
        return bool(DETAIL_PATTERNS[site].match(u.path))
    return not LISTING_PATH.search(u.path)        # 公司官網：至少不是首頁或搜尋頁


async def is_public(url: str) -> bool:
    """http(s)、80／443 埠、主機解析出來的每個 IP 都是公開位址（不是內網、本機、雲端中繼資料）。"""
    u = urlsplit(url)
    if u.scheme not in ("http", "https") or not u.hostname or u.username or u.password:
        return False
    try:
        if u.port not in (None, 80, 443):
            return False
        infos = await asyncio.get_running_loop().getaddrinfo(u.hostname, u.port or 443, type=socket.SOCK_STREAM)
    except (OSError, ValueError):
        return False
    return bool(infos) and all(ipaddress.ip_address(i[4][0].split("%")[0]).is_global for i in infos)


async def _get(client: httpx.AsyncClient, url: str) -> httpx.Response | None:
    """自己跟隨轉址，每一跳都重新檢查是不是公開位址。"""
    for _ in range(MAX_REDIRECTS + 1):
        if not await is_public(url):
            return None
        r = await client.get(url, follow_redirects=False)
        if r.is_redirect and r.headers.get("location"):
            url = urljoin(url, r.headers["location"])
            continue
        return r
    return None


async def _check(client: httpx.AsyncClient, url: str) -> str | None:
    if not url.startswith(("http://", "https://")) or not looks_like_detail(url):
        return None
    try:
        r = await _get(client, url)
    except (httpx.HTTPError, ValueError):
        return None
    if r is None or r.status_code in (404, 410) or r.status_code >= 500 and r.status_code != 503:
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
