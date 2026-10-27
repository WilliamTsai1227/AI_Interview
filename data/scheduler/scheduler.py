"""本機職缺爬蟲排程：讀同資料夾的 .env，定時或手動執行 data/job_crawlers 的爬蟲，結果寫進 docker compose 的 PostgreSQL。

指令（建議用 ./run.sh 執行，會自動建立虛擬環境）：
  ./run.sh crawl          立刻爬一次（sources/companies.json 裡 enabled 的公司）
  ./run.sh crawl acme     只爬指定公司（name）
  ./run.sh verify         立刻檢查已上架職缺是否還在
  ./run.sh status         最近的執行紀錄與職缺統計
  ./run.sh start          常駐：每天依 .env 的時間自動爬取與檢查（Ctrl+C 停止）
"""
import datetime as dt
import fcntl
import json
import logging
import os
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

HERE = Path(__file__).resolve().parent
CRAWLERS = HERE.parent / "job_crawlers"


# ---------- .env ----------
def load_env(path: Path) -> None:
    """讀 KEY=VALUE（忽略註解與空行）。已經在環境中的變數不覆蓋，方便臨時用 export 調整。"""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        elif " #" in val:
            val = val.split(" #", 1)[0].rstrip()
        os.environ.setdefault(key, val)


load_env(HERE / ".env")
sys.path.insert(0, str(CRAWLERS))

from crawler import db                                    # noqa: E402  （需要先載入 .env）
from crawler.config import Config                         # noqa: E402
from crawler.run import crawl, load_sources, verify       # noqa: E402

LOG_DIR = HERE / "logs"
LOG_DIR.mkdir(exist_ok=True)
log = logging.getLogger("scheduler")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout),
                              RotatingFileHandler(LOG_DIR / "scheduler.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")])


def _times(name: str, default: str) -> list[dt.time]:
    """CRAWL_AT=02:00,14:00 → 每天這些時間執行；空白表示不排程。"""
    raw = os.environ.get(name, default).strip()
    out = []
    for part in raw.split(","):
        part = part.strip()
        if part:
            h, m = part.split(":")
            out.append(dt.time(int(h), int(m)))
    return out


def _sources_path() -> str:
    p = os.environ.get("SOURCES_FILE", "").strip()
    if not p:
        return str(CRAWLERS / "sources" / "companies.json")
    return str((HERE / p).resolve()) if not os.path.isabs(p) else p


# ---------- 同時只跑一個（排程與手動指令共用鎖） ----------
class Lock:
    def __init__(self):
        self.f = open(LOG_DIR / ".lock", "w")

    def __enter__(self):
        try:
            fcntl.flock(self.f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("已經有一個爬蟲在執行中，請等它結束再試")
        return self

    def __exit__(self, *a):
        fcntl.flock(self.f, fcntl.LOCK_UN)


def check_db(cfg: Config) -> None:
    try:
        conn = db.connect(cfg.database_url)
        conn.run("SELECT 1 FROM job_ingest_runs LIMIT 1")
        conn.close()
    except Exception as e:
        raise SystemExit(f"連不到資料庫（{cfg.database_url.split('@')[-1]}）：{e}\n"
                         "請先在專案根目錄執行 docker compose up -d --build")


# ---------- 動作 ----------
def do_crawl(only: list[str] | None = None) -> dict:
    cfg = Config()
    check_db(cfg)
    sources = load_sources(_sources_path())
    if only:
        sources = [s for s in sources if s["name"] in set(only)]
    if not sources:
        log.warning("沒有要爬的公司：請在 %s 加入公司並設定 \"enabled\": true", _sources_path())
        return {}
    with Lock():
        log.info("開始爬取 %d 家公司%s", len(sources), "（含 AI 摘要）" if cfg.summarize_enabled else "")
        out = crawl(cfg, sources)
    log.info("完成：抓到 %s 筆，新增 %s、更新 %s（上架 %s、待審 %s）", out.get("fetched"), out.get("inserted"),
             out.get("updated"), out.get("published"), out.get("review"))
    for name, per in out.get("companies", {}).items():
        log.info("  %s：%s", name, json.dumps(per, ensure_ascii=False))
    return out


def do_verify() -> dict:
    cfg = Config()
    check_db(cfg)
    with Lock():
        out = verify(cfg, int(os.environ.get("VERIFY_LIMIT", "300")))
    log.info("檢查完成：%s", json.dumps(out, ensure_ascii=False))
    return out


def do_status() -> None:
    cfg = Config()
    check_db(cfg)
    conn = db.connect(cfg.database_url)
    print("最近 10 次執行：")
    for r in conn.run("""SELECT started_at, finished_at, status, details->>'mode', fetched_count, published_count,
                                review_count, closed_count, COALESCE(error, '')
                           FROM job_ingest_runs ORDER BY started_at DESC LIMIT 10"""):
        started = r[0].astimezone().strftime("%m/%d %H:%M")
        dur = f"{int((r[1] - r[0]).total_seconds())}s" if r[1] else "執行中"
        print(f"  {started}  {r[3] or '-':6s} {r[2]:9s} {dur:>6s}  抓到 {r[4]}、上架 {r[5]}、待審 {r[6]}、下架 {r[7]}  {r[8][:60]}")
    print("\n公司官網職缺：")
    for st, rs, n in conn.run("""SELECT status, review_status, count(*) FROM job_posts
                                  WHERE source = 'catalog' AND source_site = 'company' GROUP BY 1, 2 ORDER BY 1, 2"""):
        print(f"  {st:7s} {rs:15s} {n}")
    conn.close()
    nxt = next_runs(dt.datetime.now())
    print("\n排程：" + ("、".join(f"{k} {v:%m/%d %H:%M}" for k, v in nxt) if nxt else "未設定（CRAWL_AT／VERIFY_AT 為空）"))


def next_runs(now: dt.datetime) -> list[tuple[str, dt.datetime]]:
    out = []
    for kind, times in (("crawl", _times("CRAWL_AT", "02:00")), ("verify", _times("VERIFY_AT", "04:00"))):
        for t in times:
            when = dt.datetime.combine(now.date(), t)
            out.append((kind, when if when > now else when + dt.timedelta(days=1)))
    return sorted(out, key=lambda x: x[1])


def do_start() -> None:
    plan = next_runs(dt.datetime.now())
    if not plan:
        raise SystemExit("CRAWL_AT 與 VERIFY_AT 都是空的，沒有排程可以執行")
    check_db(Config())
    log.info("排程已啟動（Ctrl+C 停止）：%s", "、".join(f"{k} 每天 {v:%H:%M}" for k, v in plan))
    if os.environ.get("RUN_ON_START", "false").lower() == "true":
        do_crawl()
    while True:
        kind, when = next_runs(dt.datetime.now())[0]
        log.info("下一次：%s %s", kind, when.strftime("%m/%d %H:%M"))
        while (wait := (when - dt.datetime.now()).total_seconds()) > 0:
            time.sleep(min(wait, 60))          # 每分鐘醒來一次，電腦睡眠後也能接著跑
        try:
            do_crawl() if kind == "crawl" else do_verify()
        except SystemExit as e:
            log.warning(str(e))
        except Exception:
            log.exception("%s 失敗，下次排程再試", kind)


def main() -> None:
    args = sys.argv[1:]
    cmd = args[0] if args else "help"
    try:
        if cmd == "crawl":
            do_crawl(args[1:] or None)
        elif cmd == "verify":
            do_verify()
        elif cmd == "status":
            do_status()
        elif cmd == "start":
            do_start()
        else:
            print(__doc__)
    except KeyboardInterrupt:
        log.info("已停止")


if __name__ == "__main__":
    main()
