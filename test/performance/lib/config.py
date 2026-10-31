"""壓測設定：全部可以用環境變數覆寫（見 README）。"""
import os

HOST = os.getenv("PERF_HOST", "http://localhost:8019")          # 壓測環境的網址（docker-compose.perf.yml 開在 8019）
API = "/api/v1"
PASSWORD = os.getenv("PERF_PASSWORD", "perf-pass-123")
PREFIX = os.getenv("PERF_ACCOUNT_PREFIX", "perf-")
ACCOUNTS = int(os.getenv("PERF_ACCOUNTS", "300"))                # 測試帳號數量；要 ≥ 最多同時在線的使用者數
ANSWER_SEC = float(os.getenv("PERF_ANSWER_SEC", "5"))            # 模擬每題講幾秒（真人約 60 秒，壓測時縮短）
ASYNC_TIMEOUT = float(os.getenv("PERF_ASYNC_TIMEOUT", "120"))    # 等背景工作（面試準備好、報告產生）最多幾秒


def email(i: int) -> str:
    return f"{PREFIX}{i:04d}@example.com"
