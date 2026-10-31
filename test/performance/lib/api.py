"""Locust 使用者共用的 API 呼叫：帶登入憑證、統一的統計名稱、失敗就中止這個任務。

統計名稱把 ID 換成 [id]（例如 GET /targets/[id]），同一支 API 的數據才會合在一起。
"""
import time
import uuid

from locust.exception import RescheduleTask

from lib import accounts, config


class Api:
    def __init__(self, user):
        self.client = user.client
        self.environment = user.environment
        self.email = accounts.take()
        self.token = None

    def login(self) -> None:
        with self.client.post(f"{config.API}/auth/login", json={"email": self.email, "password": config.PASSWORD},
                              name="POST /auth/login", catch_response=True) as r:
            if r.status_code != 200:
                r.failure(f"{r.status_code} {r.text[:120]}")
                raise RescheduleTask()
            self.token = r.json()["access_token"]

    def call(self, method: str, path: str, name: str, expect=(200,), retry_auth=True, headers=None, **kw):
        """回傳 (狀態碼, JSON)。狀態碼不在 expect 裡算失敗並中止這個任務；401 會重新登入再試一次。"""
        h = {"Authorization": f"Bearer {self.token}", **(headers or {})}
        with self.client.request(method, f"{config.API}{path}", name=name, headers=h, catch_response=True, **kw) as r:
            if r.status_code == 401 and retry_auth:
                r.failure("401 登入憑證失效")
                self.login()
                return self.call(method, path, name, expect, retry_auth=False, headers=headers, **kw)
            if r.status_code not in expect:
                r.failure(f"{r.status_code} {r.text[:120]}")
                raise RescheduleTask()
            r.success()
            return r.status_code, (r.json() if r.content and "json" in r.headers.get("content-type", "") else None)

    def wait_for(self, fn, done, name: str):
        """輪詢背景工作直到完成，把「從開始到完成」的時間記成一筆 ASYNC 統計（例如報告產生要幾秒）。"""
        t0 = time.monotonic()
        while time.monotonic() - t0 < config.ASYNC_TIMEOUT:
            v = fn()
            if done(v):
                self._record(name, t0)
                return v
            time.sleep(0.5)
        self._record(name, t0, error=TimeoutError(f"超過 {config.ASYNC_TIMEOUT:.0f} 秒"))
        raise RescheduleTask()

    def _record(self, name: str, t0: float, error: Exception | None = None) -> None:
        self.environment.events.request.fire(request_type="ASYNC", name=name, response_time=(time.monotonic() - t0) * 1000,
                                             response_length=0, exception=error, context={})

    @staticmethod
    def idem_key() -> str:
        return str(uuid.uuid4())
