"""讀取作品集的 GitHub repo：基本資訊、語言、README、完整目錄、設定檔、主要原始碼、最近 commit。

只連 api.github.com 與 raw.githubusercontent.com（網址只取 owner/repo，不會被導去別的主機）。
原始碼走 raw.githubusercontent.com，不佔 GitHub API 次數；沒設定 GITHUB_TOKEN 時 API 每個 IP 每小時 60 次。
讀不到（私人 repo、不存在、被限流）時回傳 status=failed 與原因，不影響整份職涯分析。
"""
import asyncio
import re
from urllib.parse import urlsplit

import httpx
import structlog

from app.core.config import settings

log = structlog.get_logger()
API = "https://api.github.com"
RAW = "https://raw.githubusercontent.com"
NAME = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
REPO = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
FILE_CAP = 10_000      # 單一檔案最多讀幾個字
README_CAP = 15_000
MAX_FILES = 40         # 每個 repo 最多讀幾個檔案（總字數另由 PORTFOLIO_REPO_CHARS 限制）

# 一律跳過的目錄與檔案（第三方、產物、二進位）
SKIP_DIRS = {"node_modules", "vendor", "dist", "build", "out", ".next", ".nuxt", "target", "bin", "obj", "venv", ".venv", "env",
             "__pycache__", ".git", ".idea", ".vscode", "coverage", "site-packages", "third_party", "Pods", ".gradle", "public/static"}
SKIP_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".pdf", ".zip", ".gz", ".tar", ".mp3", ".mp4", ".wav", ".woff",
            ".woff2", ".ttf", ".eot", ".lock", ".min.js", ".min.css", ".map", ".pkl", ".pt", ".h5", ".onnx", ".bin", ".db", ".sqlite",
            ".parquet", ".csv", ".jar", ".class", ".so", ".dll", ".exe", ".ipynb_checkpoints"}
SKIP_FILES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "Pipfile.lock", "go.sum", "Cargo.lock", "composer.lock"}
CODE_EXT = {".py", ".js", ".mjs", ".ts", ".tsx", ".jsx", ".go", ".java", ".kt", ".rs", ".rb", ".php", ".cs", ".cpp", ".cc", ".c", ".h",
            ".swift", ".scala", ".vue", ".svelte", ".sql", ".sh", ".dart", ".ex", ".exs", ".lua", ".r", ".ipynb"}
# 設定檔：最能看出技術選型、部署與工程習慣
CONFIG_NAMES = {"requirements.txt", "pyproject.toml", "setup.py", "Pipfile", "package.json", "go.mod", "Cargo.toml", "pom.xml",
                "build.gradle", "build.gradle.kts", "Gemfile", "composer.json", "Dockerfile", "docker-compose.yml", "docker-compose.yaml",
                "compose.yml", "compose.yaml", "Makefile", "tsconfig.json", "vite.config.ts", "vite.config.js", "next.config.js",
                "serverless.yml", "Procfile", "nginx.conf", "alembic.ini", "pytest.ini", "tox.ini", ".env.example", "Chart.yaml"}
CONFIG_DIRS = (".github/workflows/", "k8s/", "deploy/", "infra/", "terraform/", "helm/")
CORE_NAMES = re.compile(r"(^|/)(main|app|server|index|api|router|routes|model|models|schema|schemas|controller|controllers|"
                        r"core|db|database|config|views|urls)\b", re.I)
LOGIC_NAMES = re.compile(r"(^|/)(service|services|handler|handlers|worker|workers|agent|agents|pipeline|client|queue|tasks?|usecases?|domain)\b", re.I)


def parse(url: str) -> tuple[str, str | None] | None:
    """github.com/owner[/repo] → (owner, repo|None)；不是 GitHub 網址回傳 None。"""
    u = urlsplit(url if "://" in url else f"https://{url}")
    if (u.hostname or "").lower() not in ("github.com", "www.github.com"):
        return None
    parts = [p for p in u.path.split("/") if p]
    if not parts or not NAME.match(parts[0]):
        return None
    if len(parts) == 1:
        return parts[0], None
    repo = parts[1].removesuffix(".git")
    return (parts[0], repo) if REPO.match(repo) else None


def _headers(raw: bool = False) -> dict:
    h = {"Accept": "application/vnd.github.raw" if raw else "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
         "User-Agent": "ai-interview-career"}
    if settings.github_token:
        h["Authorization"] = f"Bearer {settings.github_token}"
    return h


class ReadError(Exception):
    def __init__(self, msg: str, rate_limited: bool = False):
        super().__init__(msg)
        self.rate_limited = rate_limited


async def _api(c: httpx.AsyncClient, path: str, **params):
    r = await c.get(f"{API}{path}", params=params or None, headers=_headers())
    if r.status_code == 404:
        raise ReadError("找不到這個 repo，可能是私人 repo 或網址打錯")
    if r.status_code in (403, 429) and r.headers.get("x-ratelimit-remaining") == "0":
        raise ReadError("GitHub 讀取次數用完了，請稍後再分析（管理員可設定 GITHUB_TOKEN 提高上限）", rate_limited=True)
    if r.status_code >= 400:
        raise ReadError(f"GitHub 回應 {r.status_code}")
    return r.json()


def _skip(path: str, size: int) -> bool:
    low = path.lower()
    name = path.rsplit("/", 1)[-1]
    if name in SKIP_FILES or any(low.endswith(e) for e in SKIP_EXT):
        return True
    if any(f"/{d.lower()}/" in f"/{low}" for d in SKIP_DIRS):
        return True
    return size > 200_000


def _ext(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    return name[name.rfind("."):].lower() if "." in name else ""


def _is_config(path: str) -> bool:
    return path.rsplit("/", 1)[-1] in CONFIG_NAMES or any(path.startswith(d) or f"/{d}" in path for d in CONFIG_DIRS)


def _code_score(path: str, size: int) -> float:
    """越像核心程式碼分數越高：淺層、名稱像進入點／路由／服務、有一定份量；測試與 migration 少量收錄。"""
    depth = path.count("/")
    s = 10 - min(depth, 6)
    stem = path.rsplit(".", 1)[0]
    if LOGIC_NAMES.search(stem):
        s += 8                     # 商業邏輯所在，最能看出實力
    elif CORE_NAMES.search(stem):
        s += 5
    if re.search(r"(^|/)(tests?|__tests__|spec)(/|$)|(_test|\.test|\.spec)\.", path, re.I):
        s -= 6
    if "migration" in path.lower():
        s -= 4
    if re.search(r"(^|/)(examples?|docs?|scripts?|samples?|fixtures?|mocks?)(/|$)", path, re.I):
        s -= 5
    if size < 300:
        s -= 4
    elif 1500 <= size <= 40_000:
        s += 3
    return s


def pick_files(tree: list[dict], budget: int) -> tuple[list[str], list[str]]:
    """從整棵目錄挑要讀的檔案：先設定檔，再挑原始碼；同一個資料夾挑過的越多，下一個分數扣越多，讓各模組都讀得到。
    另外保留 1–2 個測試與 1 個 migration 當工程習慣的證據。"""
    blobs = [t for t in tree if t.get("type") == "blob" and (t.get("size") or 0) >= 80 and not _skip(t["path"], t.get("size") or 0)]
    sample = re.compile(r"(^|/)(examples?|samples?|demos?|docs?)(/|$)", re.I)
    configs, seen = [], {}
    for t in sorted((t for t in blobs if _is_config(t["path"])),
                    key=lambda t: (bool(sample.search(t["path"])), t["path"].count("/"), t["path"])):
        name = t["path"].rsplit("/", 1)[-1]
        if seen.get(name, 0) < 2 and len(configs) < 12:           # 同名設定檔（例如很多個 Dockerfile）最多讀 2 個
            configs.append(t)
            seen[name] = seen.get(name, 0) + 1
    code = [t for t in blobs if _ext(t["path"]) in CODE_EXT and not _is_config(t["path"])]
    tests = [t for t in code if re.search(r"(^|/)(tests?|__tests__)(/|$)|(_test|\.test|\.spec)\.|(^|/)test_", t["path"], re.I)][:2]
    migs = [t for t in code if "migration" in t["path"].lower() and t["path"].endswith(".sql")][:1]
    chosen, used = [], 0

    def take(t) -> None:
        nonlocal used
        cost = min(t.get("size") or 0, FILE_CAP)
        if t["path"] not in chosen and len(chosen) < MAX_FILES and (used + cost <= budget or not chosen):
            chosen.append(t["path"])
            used += cost
    for t in configs + tests + migs:
        take(t)
    pool = {t["path"]: (t, _code_score(t["path"], t.get("size") or 0)) for t in code if t["path"] not in chosen}
    per_dir: dict[str, int] = {}
    while pool and len(chosen) < MAX_FILES and used < budget:
        path, (t, _) = max(pool.items(), key=lambda kv: kv[1][1] - 4 * per_dir.get(kv[0].rsplit("/", 1)[0], 0))
        del pool[path]
        before = len(chosen)
        take(t)
        if len(chosen) > before:
            d = path.rsplit("/", 1)[0]
            per_dir[d] = per_dir.get(d, 0) + 1
    return [t["path"] for t in configs], chosen


def tree_text(tree: list[dict], limit: int = 500) -> str:
    """目錄結構（略過第三方與產物），超過 limit 行時截斷並註明。"""
    paths = [t["path"] + ("/" if t.get("type") == "tree" else "") for t in tree
             if not any(f"/{d.lower()}/" in f"/{t['path'].lower()}/" for d in SKIP_DIRS)]
    more = f"\n…（另有 {len(paths) - limit} 個路徑省略）" if len(paths) > limit else ""
    return "\n".join(paths[:limit]) + more


async def _raw(c: httpx.AsyncClient, owner: str, repo: str, ref: str, path: str, cap: int = FILE_CAP) -> str | None:
    try:
        r = await c.get(f"{RAW}/{owner}/{repo}/{ref}/{path}", headers={"User-Agent": "ai-interview-career"})
        if r.status_code != 200:
            return None
        text = r.text
        if "\x00" in text[:2000]:
            return None
        return text[:cap] + ("\n…（檔案太長，以下省略）" if len(text) > cap else "")
    except httpx.HTTPError:
        return None


async def read_repo(owner: str, repo: str) -> dict:
    """回傳 {meta, languages, readme, tree, files: [{path, content}], commits} 或丟出 ReadError。"""
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as c:
        meta = await _api(c, f"/repos/{owner}/{repo}")
        if meta.get("private"):
            raise ReadError("這是私人 repo，Ava 讀不到")
        owner, repo = meta["owner"]["login"], meta["name"]          # 改名或大小寫不同時以 GitHub 為準
        branch = meta.get("default_branch") or "main"
        langs, tree_r, commits = await asyncio.gather(
            _api(c, f"/repos/{owner}/{repo}/languages"),
            _api(c, f"/repos/{owner}/{repo}/git/trees/{branch}", recursive="1"),
            _api(c, f"/repos/{owner}/{repo}/commits", per_page="30"), return_exceptions=True)
        if isinstance(tree_r, BaseException):
            raise tree_r if isinstance(tree_r, ReadError) else ReadError("讀不到這個 repo 的檔案")
        sha = tree_r.get("sha") or branch
        tree = tree_r.get("tree") or []
        configs, chosen = pick_files(tree, settings.portfolio_repo_chars)
        readme_path = next((t["path"] for t in tree if t.get("type") == "blob" and re.match(r"^readme(\.\w+)?$", t["path"], re.I)), None)
        sem = asyncio.Semaphore(8)

        async def one(p):
            async with sem:
                return p, await _raw(c, owner, repo, sha, p, README_CAP if p == readme_path else FILE_CAP)
        got = await asyncio.gather(*(one(p) for p in ([readme_path] if readme_path else []) + chosen))
    contents = {p: t for p, t in got if t}
    readme = contents.pop(readme_path, None) if readme_path else None
    return {
        "meta": {"full_name": meta["full_name"], "url": meta["html_url"], "description": meta.get("description") or "",
                 "topics": meta.get("topics") or [], "stars": meta.get("stargazers_count", 0), "forks": meta.get("forks_count", 0),
                 "fork": bool(meta.get("fork")), "created_at": meta.get("created_at"), "pushed_at": meta.get("pushed_at"),
                 "default_branch": branch, "sha": sha, "file_count": sum(1 for t in tree if t.get("type") == "blob"),
                 "truncated": bool(tree_r.get("truncated"))},
        "languages": langs if isinstance(langs, dict) else {},
        "readme": readme or "",
        "tree": tree_text(tree),
        "files": [{"path": p, "content": contents[p], "kind": "config" if p in configs else "code"} for p in chosen if p in contents],
        "commits": [{"message": (x["commit"]["message"] or "").split("\n")[0][:120], "date": x["commit"]["author"]["date"]}
                    for x in commits] if isinstance(commits, list) else [],
    }


async def read_profile(owner: str) -> dict:
    """GitHub 個人頁：列出最近更新的公開 repo（不讀原始碼）。"""
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as c:
        repos = await _api(c, f"/users/{owner}/repos", sort="pushed", per_page="20")
    return {"repos": [{"name": r["name"], "url": r["html_url"], "description": r.get("description") or "", "language": r.get("language"),
                       "stars": r.get("stargazers_count", 0), "pushed_at": r.get("pushed_at")}
                      for r in repos if not r.get("fork")][:12]}
