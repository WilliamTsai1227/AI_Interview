"""壓力測試：人數一階一階往上加，找出系統開始撐不住的人數，最後降回少量人數確認能恢復。

  locust -f profiles/stress.py --headless
每階加 PERF_STEP_USERS（預設 25）人、維持 PERF_STEP_SEC（預設 60）秒，加到 PERF_MAX_USERS（預設 300）；
接著降到 10 人跑 60 秒（恢復期）。結束時印出每一階的結果和「開始撐不住的人數」。
壓力測試的目的是找極限，所以不判定通過或失敗。
"""
import math
import os

from _common import BrowsingUser, InterviewUser  # noqa: F401
from locust import LoadTestShape

from lib import steps

STEP_USERS = int(os.getenv("PERF_STEP_USERS", "25"))
STEP_SEC = int(os.getenv("PERF_STEP_SEC", "60"))
MAX_USERS = int(os.getenv("PERF_MAX_USERS", "300"))
RECOVERY_USERS, RECOVERY_SEC = 10, 60


class Stress(LoadTestShape):
    def tick(self):
        t = self.get_run_time()
        n_steps = math.ceil(MAX_USERS / STEP_USERS)
        if t < n_steps * STEP_SEC:
            return (min(MAX_USERS, (int(t // STEP_SEC) + 1) * STEP_USERS), STEP_USERS / 5)
        if t < n_steps * STEP_SEC + RECOVERY_SEC:
            return (RECOVERY_USERS, MAX_USERS)
        return None


steps.install(p95_limit_ms=800, fail_limit=0.01)
