"""瀏覽型使用者：登入後在各頁面之間看資料（目標職缺、題庫、面試建議、報告、職涯規劃）。

模擬大多數時間的流量：讀取為主，打的是資料庫查詢。比例跟真人差不多：首頁和目標職缺最常看。
"""
import random

from locust import HttpUser, between, task

from lib import config
from lib.api import Api


class BrowsingUser(HttpUser):
    host = config.HOST
    weight = 4
    wait_time = between(2, 6)       # 每個動作之間停 2–6 秒，像真人在看頁面

    def on_start(self):
        self.api = Api(self)
        self.api.login()
        _, t = self.api.call("GET", "/targets", "GET /targets")
        self.tid = t["items"][0]["id"] if t["items"] else None
        self.sids = []

    @task(4)
    def home(self):
        self.api.call("GET", "/me", "GET /me")
        self.api.call("GET", "/targets", "GET /targets")

    @task(3)
    def target_detail(self):
        if self.tid:
            self.api.call("GET", f"/targets/{self.tid}", "GET /targets/[id]")

    @task(3)
    def question_bank(self):
        if self.tid:
            self.api.call("GET", f"/targets/{self.tid}/questions", "GET /targets/[id]/questions")

    @task(2)
    def prep(self):
        if self.tid:
            self.api.call("GET", f"/targets/{self.tid}/prep", "GET /targets/[id]/prep")

    @task(2)
    def reports(self):
        _, r = self.api.call("GET", "/interviews", "GET /interviews")
        self.sids = [x["id"] for x in r["items"] if x.get("report_status") == "ready"]
        if self.sids:
            self.api.call("GET", f"/interviews/{random.choice(self.sids)}/report", "GET /interviews/[id]/report")

    @task(1)
    def career(self):
        self.api.call("GET", "/career/profile", "GET /career/profile")
        self.api.call("GET", "/career/plans/latest", "GET /career/plans/latest")
