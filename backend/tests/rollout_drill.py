"""部署演練：一邊跑「面試中、上傳中、評分中、登入」的流量，一邊實際部署，記錄有沒有失敗。

用法（專案根目錄，KUBECONFIG 指向 k3s）：
    backend/.venv/bin/python backend/tests/rollout_drill.py --deploy ./deploy/k3s/deploy.sh
    backend/.venv/bin/python backend/tests/rollout_drill.py --deploy "kubectl rollout restart deploy -n ai-interview"

流程：
  1. 準備：註冊 3 個使用者、各建一個目標職缺，等題目出好（這段不算在演練裡）
  2. 同時開始 3 場面試（作答間隔拉長，橫跨整個部署）、3 條上傳迴圈、1 條登入迴圈；送出 2 題回答
     （評分工作開始跑）之後執行部署指令
  3. 部署完成後，等面試報告評分完成，再到資料庫查這段時間的背景工作
  4. 安全重試檢查：把一場已完成面試的轉錄／評分工作改回待重跑，確認分數不變、沒有重複呼叫 AI

用戶端重送規則和前端（frontend/js/api.js）相同：GET 或帶 Idempotency-Key 的請求，遇到連線錯誤或代理層的
502／503／504 最多送 3 次。報表分開列出「原始錯誤」（重送前）和「使用者看得到的失敗」（重送後仍失敗）。
開了 OpenAI 金鑰時會真的呼叫 AI，一次演練約數十次呼叫。
"""
import argparse
import io
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections import Counter

import httpx

WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 4000
DOCX_CT = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
JD = ("Backend Engineer｜Acme Cloud\n工作內容：\n- 設計並維護 Python API\n- 優化 PostgreSQL 查詢\n"
      "條件要求：\n- 3 年以上 Python 經驗\n- 熟悉 Redis 與 Docker")
NS = "ai-interview"

T0 = time.time()
_lock = threading.Lock()
EVENTS: list[dict] = []          # 每一筆請求的結果
deploy_done = threading.Event()
answered = threading.Semaphore(0)   # 每送出一題回答 +1：部署要等評分工作開始跑才觸發
AUDIO = (WEBM, "audio/webm")


def real_audio() -> tuple[bytes, str] | None:
    """macOS 用 say＋afconvert 產生一段真的中文語音（m4a），STT 和評分才會真的花時間；其他系統退回假錄音。"""
    if not (shutil.which("say") and shutil.which("afconvert")):
        return None
    d = tempfile.mkdtemp()
    text = "我在上一份工作負責後端 API，用 Redis 做快取、調整 PostgreSQL 索引，把回應時間從八百毫秒降到兩百毫秒，提升了百分之七十五。"
    subprocess.run(["say", "-v", "Meijia", "-o", f"{d}/a.aiff", text], check=True, capture_output=True)
    subprocess.run(["afconvert", "-f", "m4af", "-d", "aac", f"{d}/a.aiff", f"{d}/a.m4a"], check=True, capture_output=True)
    with open(f"{d}/a.m4a", "rb") as f:
        return f.read(), "audio/mp4"


def now() -> float:
    return round(time.time() - T0, 1)


def record(**e) -> None:
    with _lock:
        EVENTS.append({"t": now(), **e})


def docx_bytes() -> bytes:
    import docx
    d = docx.Document()
    for line in ["測試 小明", "後端工程師，4 年 Python、PostgreSQL、Redis 經驗。",
                 "Acme 後端工程師 2022 至今：API 回應時間從 800ms 降到 200ms。"]:
        d.add_paragraph(line)
    b = io.BytesIO()
    d.save(b)
    return b.getvalue()


class Client:
    def __init__(self, base: str, flow: str):
        self.c = httpx.Client(base_url=base + "/api/v1", timeout=60)
        self.flow = flow
        self.token: str | None = None
        self.soft = False       # 外層還會再重試（例如送出回答）：這次失敗只算原始錯誤

    def _fail(self, op: str, error: str) -> None:
        record(flow=self.flow, op=op, error=error, **({"raw": True} if self.soft else {"failed": True}))

    def req(self, method: str, path: str, *, idem: str | None = None, ok=(200, 201, 202, 204), **kw):
        h = kw.pop("headers", {})
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        if idem:
            h["Idempotency-Key"] = idem
        safe = method == "GET" or bool(idem)
        name = f"{method} {_route(path)}"
        for i in range(3):
            try:
                r = self.c.request(method, path, headers=h, **kw)
            except httpx.HTTPError as e:
                record(flow=self.flow, op=name, raw=True, error=type(e).__name__)
                if safe and i < 2:
                    time.sleep(0.4 * 2 ** i)
                    continue
                self._fail(name, type(e).__name__)
                raise
            proxy = r.status_code in (502, 504) or (r.status_code == 503 and "json" not in r.headers.get("content-type", ""))
            if proxy:
                record(flow=self.flow, op=name, raw=True, error=f"HTTP {r.status_code}")
                if safe and i < 2:
                    time.sleep(0.4 * 2 ** i)
                    continue
            break
        if r.status_code not in ok:
            self._fail(name, f"HTTP {r.status_code} {r.text[:160]}")
            raise RuntimeError(f"{name} → {r.status_code} {r.text[:200]}")
        record(flow=self.flow, op=name)
        return r.json() if r.content and "json" in r.headers.get("content-type", "") else None

    def upload(self, purpose: str, data: bytes, ct: str, **extra) -> str:
        """同前端 uploadFile：PUT 預簽名網址最多 4 次，網址過期（403）就重拿上傳單。"""
        t = self.req("POST", "/uploads", json={"purpose": purpose, "content_type": ct, "size_bytes": len(data), **extra})
        for i in range(4):
            try:
                r = httpx.put(t["url"], content=data, headers=t["headers"], timeout=60)
            except httpx.HTTPError as e:
                r = None
                record(flow=self.flow, op="PUT s3", raw=True, error=type(e).__name__)
            if r is not None and r.status_code == 200:
                record(flow=self.flow, op="PUT s3")
                return t["upload_id"]
            if r is not None:
                record(flow=self.flow, op="PUT s3", raw=True, error=f"HTTP {r.status_code}")
                if r.status_code < 500 and r.status_code != 403:
                    break
                if r.status_code == 403:
                    t = self.req("POST", "/uploads", json={"purpose": purpose, "content_type": ct, "size_bytes": len(data), **extra})
            time.sleep(0.5 * 2 ** i)
        self._fail("PUT s3", "upload gave up")
        raise RuntimeError("upload failed")


def _route(path: str) -> str:
    """把 UUID 換成 {id}，報表才能依端點分組。"""
    parts = path.split("?")[0].split("/")
    return "/".join("{id}" if len(p) == 36 and p.count("-") == 4 else p for p in parts)


def wait(fn, ok, what: str, timeout: float):
    t = time.time()
    while time.time() - t < timeout:
        v = fn()
        if ok(v):
            return v
        time.sleep(2)
    raise RuntimeError(f"timeout waiting for {what}")


def psql(sql: str) -> list[list[str]]:
    out = subprocess.run(["kubectl", "exec", "-n", NS, "db-0", "--", "psql", "-U", "ai", "ai_interview", "-qAt", "-F", "\t", "-c", sql],
                         capture_output=True, text=True, check=True).stdout
    return [line.split("\t") for line in out.splitlines() if line]


# ---------- 準備 ----------
def setup_user(base: str, i: int) -> tuple[Client, str]:
    a = Client(base, f"setup{i}")
    r = a.req("POST", "/auth/register", json={"email": f"drill-{uuid.uuid4().hex[:8]}@example.com",
                                              "password": "pass12345", "display_name": f"演練 {i}"})
    a.token = r["access_token"]
    t = a.req("POST", "/targets", json={"raw_text": JD})
    wait(lambda: a.req("GET", f"/targets/{t['id']}"), lambda v: v["question_set"]["status"] == "ready", "questions", 300)
    return a, t["id"]


# ---------- 演練中的流量 ----------
def interview_flow(a: Client, tid: str, think: float, results: list, idx: int) -> None:
    a.flow = "interview"
    sid = None
    try:
        r = a.req("POST", "/interviews", json={"target_id": tid, "length_mode": "quick", "persona": "warm",
                                               "language": "zh", "recording_consent": True})
        sid = r["session"]["id"]
        s = wait(lambda: a.req("GET", f"/interviews/{sid}"), lambda v: v["status"] == "ready", "session ready", 300)
        s = a.req("POST", f"/interviews/{sid}/start", json={"expected_version": s["state_version"]})["session"]
        answers = 0
        while s["status"] == "in_progress":
            q = s["current_question"]
            s = a.req("POST", f"/interviews/{sid}/questions/{q['id']}/playback-finished", json={"expected_version": s["state_version"]})["session"]
            r = a.req("POST", f"/interviews/{sid}/questions/{q['id']}/answers", json={"expected_version": s["state_version"]})
            att, s = r["attempt"], r["session"]
            # 「正在作答」：跟真人一樣講一陣子，期間送心跳，讓部署發生在作答中
            end = time.time() + think
            while time.time() < end:
                time.sleep(min(5, max(0, end - time.time())))
                a.req("POST", f"/interviews/{sid}/heartbeat", json={"phase": s["phase"], "events": []})
            s = complete_answer(a, sid, att["id"], s, q)
            answered.release()
            answers += 1
        results.append({"user": idx, "session_id": sid, "status": s["status"], "answers": answers})
    except Exception as e:
        results.append({"user": idx, "session_id": sid, "error": str(e)[:300]})


def complete_answer(a: Client, sid: str, aid: str, s: dict, q: dict) -> dict:
    """同前端 submitAnswer：同一個 Idempotency-Key 最多送 5 次；上傳單據不見就重傳。"""
    key, upl = str(uuid.uuid4()), None
    body = {"expected_version": s["state_version"], "client_duration_ms": 30000,
            "client_transcript": f"我在上一份工作負責 {q.get('category') or '後端'} 相關的專案，把 API 回應時間從 800ms 降到 200ms。"}
    last = None
    for i in range(5):
        a.soft = i < 4
        try:
            upl = upl or a.upload("answer_audio", AUDIO[0], AUDIO[1], attempt_id=aid)
            return a.req("POST", f"/interviews/{sid}/attempts/{aid}/complete", idem=key, json={**body, "upload_id": upl})["session"]
        except Exception as e:
            last = e
            if "UPLOAD_NOT_FOUND" in str(e):
                upl = None
            time.sleep(2 ** i)
        finally:
            a.soft = False
    raise RuntimeError(f"complete failed: {last}")


def wait_report(a: Client, sid: str, results: list) -> None:
    try:
        rep = wait(lambda: a.req("GET", f"/interviews/{sid}/report"), lambda v: v["status"] == "ready", "report", 600)
        results.append({"session_id": sid, "report": "ready", "overall": rep["overall_score"]})
    except Exception as e:
        results.append({"session_id": sid, "report": "error", "error": str(e)[:300]})


def upload_loop(base: str, a: Client, every: float, stats: Counter) -> None:
    b = Client(base, "upload")
    b.token = a.token
    data = docx_bytes()
    while not deploy_done.is_set():
        try:
            b.upload("resume", data, DOCX_CT, filename="履歷.docx")
            stats["ok"] += 1
        except Exception:
            stats["failed"] += 1
        time.sleep(every)


def auth_loop(base: str, stats: Counter) -> None:
    """登入：每秒 refresh 一次（refresh token 會輪替）再打 /me。被登出就記一次、重新登入。"""
    a = Client(base, "auth")
    email, pw = f"drill-auth-{uuid.uuid4().hex[:8]}@example.com", "pass12345"
    a.token = a.req("POST", "/auth/register", json={"email": email, "password": pw, "display_name": "演練 登入"})["access_token"]
    while not deploy_done.is_set():
        try:
            a.token = a.req("POST", "/auth/refresh")["access_token"]
            a.req("GET", "/me")
            stats["ok"] += 1
        except Exception as e:
            stats["failed"] += 1
            if "401" in str(e):
                stats["logged_out"] += 1
                a.c.cookies.clear()
                a.token = a.req("POST", "/auth/login", json={"email": email, "password": pw})["access_token"]
        time.sleep(1)


def run_deploy(cmd: str, out: dict) -> None:
    t = now()
    p = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    out.update(start=t, end=now(), code=p.returncode, tail=(p.stdout + p.stderr)[-1500:])
    deploy_done.set()


# ---------- 安全重試檢查 ----------
def recheck_retry(sid: str) -> dict:
    """把這場面試已完成的轉錄／評分工作改回待重跑（模擬 worker 中斷後重新投遞），確認結果不變、沒有多呼叫 AI。"""
    q_scores = f"SELECT a.id, e.status, e.score FROM evaluations e JOIN answer_attempts a ON a.id = e.attempt_id WHERE a.session_id = '{sid}' ORDER BY a.id"
    q_calls = f"SELECT count(*) FROM llm_calls WHERE session_id = '{sid}' AND purpose IN ('stt','evaluate','report')"
    before, calls_before = psql(q_scores), psql(q_calls)[0][0]
    ids = psql(f"""UPDATE background_jobs SET status = 'retrying', run_at = now(), dispatched_at = NULL, worker_id = NULL
                    WHERE kind IN ('transcribe_attempt','evaluate_attempt') AND status = 'succeeded'
                      AND payload->>'attempt_id' IN (SELECT id::text FROM answer_attempts WHERE session_id = '{sid}')
                RETURNING id""")
    id_list = ",".join(r[0] for r in ids) or "0"
    wait(lambda: psql(f"SELECT count(*) FROM background_jobs WHERE id IN ({id_list}) AND status <> 'succeeded'")[0][0],
         lambda v: v == "0", "rerun jobs", 120)
    after, calls_after = psql(q_scores), psql(q_calls)[0][0]
    return {"rerun_jobs": len(ids), "scores_unchanged": before == after, "ai_calls_before": calls_before, "ai_calls_after": calls_after}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8009")
    ap.add_argument("--deploy", required=True, help="部署指令，例如 ./deploy/k3s/deploy.sh")
    ap.add_argument("--users", type=int, default=3)
    ap.add_argument("--think", type=float, default=20, help="每題作答秒數（拉長讓面試橫跨部署）")
    ap.add_argument("--deploy-after", type=int, default=2, help="送出幾題回答之後開始部署（讓部署碰上評分中的工作）")
    ap.add_argument("--out", default="rollout-drill.json")
    args = ap.parse_args()
    global AUDIO
    AUDIO = real_audio() or AUDIO
    print(f"[{now()}s] 錄音：{AUDIO[1]}（{len(AUDIO[0])} bytes）", flush=True)

    print(f"[{now()}s] 準備 {args.users} 個使用者與題目⋯", flush=True)
    users = [setup_user(args.base, i) for i in range(args.users)]
    with _lock:
        EVENTS.clear()                    # 準備階段不算
    global T0
    T0 = time.time()                      # 時間從演練開始算
    t_start = psql("SELECT now()")[0][0]
    print(f"[{now()}s] 開始：面試 × {args.users}、上傳迴圈 × {args.users}、登入迴圈 × 1，然後部署", flush=True)

    interviews: list = []
    uploads, auth = Counter(), Counter()
    dep: dict = {}
    threads = [threading.Thread(target=interview_flow, args=(a, tid, args.think, interviews, i)) for i, (a, tid) in enumerate(users)]
    threads += [threading.Thread(target=upload_loop, args=(args.base, a, 6, uploads)) for a, _ in users]
    threads.append(threading.Thread(target=auth_loop, args=(args.base, auth)))
    for t in threads:
        t.start()
    for _ in range(args.deploy_after):     # 先讓面試、上傳進行中，回答送出、評分開始跑了再部署
        answered.acquire()
    print(f"[{now()}s] 已送出 {args.deploy_after} 題回答，開始部署", flush=True)
    dt = threading.Thread(target=run_deploy, args=(args.deploy, dep))
    dt.start()
    dt.join()
    print(f"[{now()}s] 部署結束（exit {dep['code']}），等面試和報告完成⋯", flush=True)
    for t in threads:
        t.join()
    reports: list = []
    for r in interviews:
        if r.get("session_id") and r.get("status") == "completed":
            wait_report(users[r["user"]][0], r["session_id"], reports)

    jobs = psql(f"""SELECT kind, status, count(*), count(*) FILTER (WHERE last_error = 'released on worker shutdown'),
                           count(*) FILTER (WHERE last_error = 'worker lost'), max(attempts)
                      FROM background_jobs WHERE created_at >= '{t_start}' GROUP BY 1, 2 ORDER BY 1, 2""")
    released = psql(f"SELECT count(*) FROM background_jobs WHERE created_at >= '{t_start}' AND last_error IS NOT NULL")
    ready = [r["session_id"] for r in reports if r.get("report") == "ready"]
    recheck = recheck_retry(ready[0]) if ready else None

    raw = Counter((e["op"], e["error"]) for e in EVENTS if e.get("raw"))
    failed = Counter((e["op"], e["error"]) for e in EVENTS if e.get("failed"))
    total = Counter(e["flow"] for e in EVENTS if not e.get("raw") and not e.get("failed"))
    summary = {
        "deploy": {k: dep[k] for k in ("start", "end", "code")},
        "requests_ok": dict(total), "raw_errors_before_retry": {f"{k[0]} | {k[1]}": v for k, v in raw.items()},
        "user_visible_failures": {f"{k[0]} | {k[1]}": v for k, v in failed.items()},
        "interviews": interviews, "reports": reports, "uploads": dict(uploads), "auth": dict(auth),
        "jobs": [{"kind": k, "status": s, "n": int(n), "released": int(rl), "reclaimed": int(rc), "max_attempts": int(ma)}
                 for k, s, n, rl, rc, ma in jobs],
        "jobs_with_error_note": int(released[0][0]), "retry_recheck": recheck,
    }
    with open(args.out, "w") as f:
        json.dump({"summary": summary, "deploy_tail": dep.get("tail"), "events": EVENTS}, f, ensure_ascii=False, indent=1)
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    bad = failed or any("error" in r for r in interviews + reports) or (recheck and not recheck["scores_unchanged"]) \
        or any(j["status"] == "dead" for j in summary["jobs"]) or dep["code"] != 0
    print("\n結果：" + ("有失敗，見上方" if bad else "沒有使用者看得到的失敗"))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
