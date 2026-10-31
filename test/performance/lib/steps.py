"""壓力測試用：每 5 秒記錄一次「目前人數、每秒請求數、失敗率、p95」，結束時按階段整理成表格，
並找出第一個不合格的階段 → 系統開始撐不住的人數。結果另存 CSV 到 results/。

只統計 API 請求：[async] 背景工作的等待時間以秒計，混進來會把 p95 拉高。
"""
import csv
import datetime as dt
import time
from pathlib import Path

import gevent
from locust import events

RESULTS = Path(__file__).resolve().parents[1] / "results"


def install(p95_limit_ms: float, fail_limit: float, interval: float = 5) -> None:
    samples: list[dict] = []
    window: list[tuple[float, bool]] = []      # 這 5 秒內每個 API 請求的（回應毫秒, 是否失敗）

    @events.request.add_listener
    def _on_request(request_type, response_time, exception, **_kw):
        if request_type != "ASYNC":
            window.append((response_time, exception is not None))

    @events.test_start.add_listener
    def _start(environment, **_kw):
        def sample():
            t0 = time.monotonic()
            while True:
                gevent.sleep(interval)
                batch = window[:]
                window.clear()
                times = sorted(rt for rt, _ in batch)
                samples.append({"秒": round(time.monotonic() - t0), "人數": environment.runner.user_count,
                                "每秒請求": round(len(batch) / interval, 1),
                                "失敗率": round(sum(f for _, f in batch) / len(batch), 4) if batch else 0.0,
                                "p95_ms": round(times[int(len(times) * 0.95) - 1 if len(times) > 1 else 0]) if times else 0})
        gevent.spawn(sample)

    @events.quitting.add_listener
    def _report(environment, **_kw):
        if not samples:
            return
        steps: dict[int, list[dict]] = {}
        for s in samples:
            steps.setdefault(s["人數"], []).append(s)
        print("\n========== 各階段結果 ==========")
        print(f"  {'人數':>6} {'每秒請求':>8} {'失敗率':>8} {'p95(ms)':>9}")
        breaking = None
        for users, ss in steps.items():
            rps = sum(x["每秒請求"] for x in ss) / len(ss)
            fail = max(x["失敗率"] for x in ss)
            p95 = max(x["p95_ms"] for x in ss)
            bad = fail > fail_limit or p95 > p95_limit_ms
            if bad and breaking is None and users:
                breaking = users
            print(f"  {users:>6} {rps:>8.1f} {fail:>8.2%} {p95:>9.0f}{'  ← 不合格' if bad else ''}")
        print(f"\n  開始撐不住的人數：{breaking if breaking else '這次測試範圍內都撐得住'}"
              f"（標準：p95 ≤ {p95_limit_ms:.0f} ms、失敗率 ≤ {fail_limit:.0%}）")
        RESULTS.mkdir(exist_ok=True)
        out = RESULTS / f"stress-steps-{dt.datetime.now():%Y%m%d-%H%M%S}.csv"
        with out.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(samples[0]))
            w.writeheader()
            w.writerows(samples)
        print(f"  每 5 秒的原始紀錄：{out}")
        print("================================")
