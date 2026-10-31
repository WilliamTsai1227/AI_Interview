"""面試型使用者：完整走一場快速面試（3 題＋追問），每題上傳錄音，最後等報告產生。

這是最重的流程：每一步都會改面試狀態、上傳檔案到 MinIO、觸發背景工作（轉錄、評分、報告）。
除了每支 API 的速度，也記錄兩個背景工作的完成時間（統計表裡類型是 ASYNC）：
  [async] 面試準備好  建立面試 → 可以開始
  [async] 報告產生    面試結束 → 報告完成
"""
import random
import time

from locust import HttpUser, between, task
from locust.exception import RescheduleTask

from lib import config
from lib.api import Api

WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 20000      # 假錄音：只要通過檔頭檢查（約 20 KB）


class InterviewUser(HttpUser):
    host = config.HOST
    weight = 1
    wait_time = between(5, 15)      # 一場面試結束後休息一下再開下一場

    def on_start(self):
        self.api = Api(self)
        self.api.login()
        _, t = self.api.call("GET", "/targets", "GET /targets")
        if not t["items"]:
            raise RescheduleTask()
        self.tid = t["items"][0]["id"]

    @task
    def interview(self):
        a = self.api
        code, r = a.call("POST", "/interviews", "POST /interviews", expect=(201, 409),
                         json={"target_id": self.tid, "length_mode": "quick", "persona": "warm", "language": "zh",
                               "recording_consent": True})
        if code == 409:     # 上一輪中斷留下的面試：結束它，下一輪再開新的
            sid = r["error"]["details"].get("session_id")
            if sid:
                a.call("POST", f"/interviews/{sid}/end", "POST /interviews/[id]/end", json={"reason": "user_ended"})
            return
        sid = r["session"]["id"]
        s = a.wait_for(lambda: a.call("GET", f"/interviews/{sid}", "GET /interviews/[id]")[1],
                       lambda v: v["status"] == "ready", "[async] 面試準備好")
        s = a.call("POST", f"/interviews/{sid}/start", "POST /interviews/[id]/start",
                   json={"expected_version": s["state_version"]})[1]["session"]

        while s["status"] == "in_progress":
            q = s["current_question"]
            s = a.call("POST", f"/interviews/{sid}/questions/{q['id']}/playback-finished",
                       "POST /interviews/[id]/questions/[id]/playback-finished",
                       json={"expected_version": s["state_version"]})[1]["session"]
            r = a.call("POST", f"/interviews/{sid}/questions/{q['id']}/answers", "POST /interviews/[id]/questions/[id]/answers",
                       expect=(201,), json={"expected_version": s["state_version"]})[1]
            att, s = r["attempt"], r["session"]
            time.sleep(config.ANSWER_SEC * random.uniform(0.6, 1.4))      # 使用者在講話
            t = a.call("POST", "/uploads", "POST /uploads", expect=(201,),
                       json={"purpose": "answer_audio", "content_type": "audio/webm", "size_bytes": len(WEBM),
                             "attempt_id": att["id"]})[1]
            with self.client.put(t["url"], data=WEBM, headers=t["headers"], name="PUT [minio] 錄音檔", catch_response=True) as up:
                if up.status_code != 200:
                    up.failure(f"{up.status_code} {up.text[:120]}")
                    raise RescheduleTask()
            r = a.call("POST", f"/interviews/{sid}/attempts/{att['id']}/complete", "POST /interviews/[id]/attempts/[id]/complete",
                       headers={"Idempotency-Key": a.idem_key()},
                       json={"upload_id": t["upload_id"], "expected_version": s["state_version"], "client_duration_ms": 30000,
                             "client_transcript": f"關於{q['category']}，我在上一份工作負責相關專案，把 API 回應時間從 800ms 降到 200ms。"})[1]
            s = r["session"]

        a.wait_for(lambda: a.call("GET", f"/interviews/{sid}/report", "GET /interviews/[id]/report", expect=(200, 404))[1] or {},
                   lambda v: v.get("status") == "ready", "[async] 報告產生")
