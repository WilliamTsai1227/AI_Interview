"""準備測試資料：建立 PERF_ACCOUNTS 個測試帳號（perf-0000@example.com …），每個帳號一個目標職缺，等題目出好。

  python prepare.py            # 已經存在的帳號和目標職缺會略過，可以重複執行
壓測前跑一次就好。壓測環境沒有 OpenAI 金鑰，出題走假資料，幾秒就好。
"""
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from lib import config

JDS = [
    "Backend Engineer｜Acme Cloud\n工作內容：\n- 設計並維護 Python API\n- 優化 PostgreSQL 查詢\n條件要求：\n- 3 年以上 Python 經驗\n- 熟悉 Redis 與 Docker",
    "Frontend Engineer｜Shoply\n工作內容：\n- 開發電商前台與後台\n- 建立元件庫\n條件要求：\n- 熟悉 React、TypeScript\n- 重視效能與無障礙",
    "Data Engineer｜Fintrack\n工作內容：\n- 建置資料管線與數據倉儲\n- 維護 Airflow 排程\n條件要求：\n- 熟悉 SQL、Python\n- 有 Spark 或 BigQuery 經驗佳",
]
BASE = config.HOST + config.API


def setup(i: int) -> str:
    s = requests.Session()
    email = config.email(i)
    r = s.post(f"{BASE}/auth/login", json={"email": email, "password": config.PASSWORD}, timeout=30)
    if r.status_code != 200:
        r = s.post(f"{BASE}/auth/register", json={"email": email, "password": config.PASSWORD, "display_name": f"壓測 {i}"}, timeout=30)
        r.raise_for_status()
    s.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
    items = s.get(f"{BASE}/targets", timeout=30).json()["items"]
    if not items:
        s.post(f"{BASE}/targets", json={"raw_text": JDS[i % len(JDS)]}, timeout=30).raise_for_status()
    t0 = time.time()
    while time.time() - t0 < config.ASYNC_TIMEOUT:
        t = s.get(f"{BASE}/targets", timeout=30).json()["items"][0]
        if t["question_set"]["status"] == "ready" and t["question_set"]["item_count"] >= 3:
            return "ok"
        time.sleep(1)
    return "題目沒有在時間內出好"


def main() -> None:
    try:
        h = requests.get(f"{BASE}/health", timeout=5).json()
    except requests.RequestException as e:
        sys.exit(f"連不到壓測環境 {config.HOST}：{e}\n請先啟動壓測環境（見 test/performance/README.md）")
    if h.get("ai") != "fake":
        sys.exit(f"{config.HOST} 正在使用真的 OpenAI（ai={h.get('ai')}）。壓測會花錢，請改用 docker-compose.perf.yml 的環境。")
    print(f"建立 {config.ACCOUNTS} 個測試帳號與目標職缺（{config.HOST}）⋯")
    t0, bad = time.time(), []
    with ThreadPoolExecutor(10) as pool:
        futs = {pool.submit(setup, i): i for i in range(config.ACCOUNTS)}
        for n, f in enumerate(as_completed(futs), 1):
            try:
                res = f.result()
            except Exception as e:  # noqa: BLE001
                res = repr(e)[:120]
            if res != "ok":
                bad.append((futs[f], res))
            if n % 50 == 0 or n == len(futs):
                print(f"  {n}/{len(futs)}")
    print(f"完成，{time.time() - t0:.0f} 秒；失敗 {len(bad)} 個" + "".join(f"\n  {config.email(i)}：{e}" for i, e in bad[:10]))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
