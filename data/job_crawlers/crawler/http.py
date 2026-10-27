"""禮貌的 HTTP 抓取：遵守 robots.txt、每個網站限速、表明身分、遇到機器人驗證就停止（不繞過）。

只用標準庫（urllib），方便打包成 AWS Lambda。
"""
import gzip
import time
import urllib.error
import urllib.request
import urllib.robotparser
from dataclasses import dataclass
from urllib.parse import urlsplit

from .config import Config

# 機器人防護頁的特徵：看到就停止抓這個網站，不嘗試繞過
CHALLENGE_MARKERS = ("Just a moment...", "cf-chl", "challenge-platform", "Attention Required! | Cloudflare",
                     "captcha", "px-captcha", "Access Denied")


class BlockedError(Exception):
    """網站拒絕自動存取（robots.txt 不允許、或出現機器人驗證）。"""


@dataclass
class Response:
    url: str
    status: int
    text: str
    content_type: str


class Fetcher:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._robots: dict[str, urllib.robotparser.RobotFileParser] = {}
        self._last: dict[str, float] = {}
        self._blocked: dict[str, str] = {}
        self.pages: dict[str, int] = {}

    # ---------- robots.txt ----------
    def _origin(self, url: str) -> str:
        p = urlsplit(url)
        return f"{p.scheme}://{p.netloc}"

    def robots(self, url: str) -> urllib.robotparser.RobotFileParser:
        origin = self._origin(url)
        if origin not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            try:
                r = self._raw(origin + "/robots.txt", check_robots=False)
                if r.status in (401, 403):
                    rp.disallow_all = True              # 連 robots.txt 都不給看：視為不允許
                elif r.status >= 400:
                    rp.allow_all = True                 # 沒有 robots.txt：預設允許
                else:
                    if any(m in r.text for m in CHALLENGE_MARKERS):
                        rp.disallow_all = True
                    else:
                        rp.parse(r.text.splitlines())
            except Exception:
                rp.allow_all = True
            self._robots[origin] = rp
        return self._robots[origin]

    def sitemaps(self, url: str) -> list[str]:
        return list(self.robots(url).site_maps() or [])

    def allowed(self, url: str) -> bool:
        return self.robots(url).can_fetch(self.cfg.user_agent, url)

    # ---------- 抓取 ----------
    def _wait(self, host: str) -> None:
        rp = self._robots.get(self._origin("https://" + host)) or self._robots.get(self._origin("http://" + host))
        delay = self.cfg.request_delay_sec
        if rp:
            cd = rp.crawl_delay(self.cfg.user_agent)
            if cd:
                delay = max(delay, float(cd))
        gap = time.monotonic() - self._last.get(host, 0)
        if gap < delay:
            time.sleep(delay - gap)
        self._last[host] = time.monotonic()

    def _raw(self, url: str, check_robots: bool = True) -> Response:
        p = urlsplit(url)
        if p.scheme not in ("https", "http") or (p.scheme == "http" and not self.cfg.allow_http):
            raise BlockedError(f"不支援的網址：{url}")
        if p.netloc in self._blocked:
            raise BlockedError(self._blocked[p.netloc])
        if check_robots and not self.allowed(url):
            raise BlockedError(f"robots.txt 不允許：{url}")
        self._wait(p.netloc)
        req = urllib.request.Request(url, headers={"User-Agent": self.cfg.user_agent,
                                                   "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                                                   "Accept-Encoding": "gzip"})
        try:
            with urllib.request.urlopen(req, timeout=self.cfg.timeout_sec) as r:
                body = r.read(5_000_000)
                status, ctype, final = r.status, r.headers.get("Content-Type", ""), r.geturl()
                if r.headers.get("Content-Encoding") == "gzip" or url.endswith(".gz"):
                    try:
                        body = gzip.decompress(body)
                    except OSError:
                        pass
        except urllib.error.HTTPError as e:
            body = e.read(200_000) if e.fp else b""
            status, ctype, final = e.code, e.headers.get("Content-Type", "") if e.headers else "", url
        text = body.decode("utf-8", errors="replace")
        return Response(final, status, text, ctype)

    def get(self, url: str) -> Response:
        host = urlsplit(url).netloc
        if self.pages.get(host, 0) >= self.cfg.max_pages_per_site:
            raise BlockedError(f"已達每個網站的頁數上限（{self.cfg.max_pages_per_site}）")
        self.pages[host] = self.pages.get(host, 0) + 1
        r = self._raw(url)
        if r.status in (403, 429, 503) and any(m in r.text for m in CHALLENGE_MARKERS):
            reason = f"網站有機器人驗證，停止抓取（{host}）"
            self._blocked[host] = reason
            raise BlockedError(reason)
        if r.status == 429:
            self._blocked[host] = f"網站要求降低頻率（429），停止抓取（{host}）"
            raise BlockedError(self._blocked[host])
        return r

    def status(self, url: str) -> int:
        """只取狀態碼（定期檢查職缺是否還在）。"""
        try:
            return self._raw(url).status
        except BlockedError:
            raise
        except Exception:
            return 0
