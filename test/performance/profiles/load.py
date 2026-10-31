"""負載測試：用預期的正常人數跑一段時間，看回應速度和錯誤率是否合格。

  locust -f profiles/load.py --headless
人數、時間可以調：PERF_USERS（預設 50）、PERF_RAMP（幾秒內加到滿，預設 60）、PERF_DURATION（滿人數維持幾秒，預設 600）。
瀏覽與面試使用者的比例是 4:1。
"""
import os

from _common import BrowsingUser, InterviewUser  # noqa: F401（Locust 從這個檔案找使用者類別）
from locust import LoadTestShape

from lib import checks

USERS = int(os.getenv("PERF_USERS", "50"))
RAMP = int(os.getenv("PERF_RAMP", "60"))
DURATION = int(os.getenv("PERF_DURATION", "600"))


class Load(LoadTestShape):
    def tick(self):
        return (USERS, max(1, USERS / RAMP)) if self.get_run_time() < RAMP + DURATION else None


# 合格標準：一般頁面 0.5 秒內、面試中的動作 1 秒內、報告 30 秒內產生，失敗率 1% 以下
checks.install([
    (checks.TOTAL, "fail_ratio", 0.01),
    (checks.TOTAL, "p95", 800),
    ("GET /targets", "p95", 500),
    ("GET /targets/[id]/questions", "p95", 500),
    ("GET /interviews/[id]/report", "p95", 500),
    ("POST /interviews/[id]/attempts/[id]/complete", "p95", 1000),
    ("[async] 面試準備好", "p95", 10000),
    ("[async] 報告產生", "p95", 30000),
])
