"""冒煙測試：1 個瀏覽使用者＋1 個面試使用者跑 1 分鐘，確認腳本和環境都正常。每次正式測試前先跑。

  locust -f profiles/smoke.py --headless
合格：完全沒有錯誤。
"""
import os

from _common import BrowsingUser, InterviewUser
from locust import LoadTestShape

from lib import checks

DURATION = int(os.getenv("PERF_DURATION", "60"))
BrowsingUser.fixed_count = 1
InterviewUser.fixed_count = 1


class Smoke(LoadTestShape):
    def tick(self):
        return (2, 2) if self.get_run_time() < DURATION else None


checks.install([(checks.TOTAL, "fail_ratio", 0.0)])
