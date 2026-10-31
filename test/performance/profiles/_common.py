"""每個 profile 共用：讓 Locust 找得到 lib／scenarios、匯入兩種使用者，並在開始前確認不會打到真的 OpenAI。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests  # noqa: E402
from locust import events  # noqa: E402

from lib import config  # noqa: E402
from scenarios.browse import BrowsingUser  # noqa: E402
from scenarios.interview import InterviewUser  # noqa: E402

__all__ = ["BrowsingUser", "InterviewUser"]


@events.test_start.add_listener
def _refuse_real_ai(environment, **_kw):
    """壓到使用真 OpenAI 的環境會花很多錢、也會被 OpenAI 限流 → 直接停止。"""
    try:
        ai = requests.get(f"{config.HOST}{config.API}/health", timeout=5).json().get("ai")
    except requests.RequestException as e:
        print(f"\n連不到 {config.HOST}：{e}（先啟動壓測環境，見 test/performance/README.md）")
        environment.process_exit_code = 2
        environment.runner.quit()
        return
    if ai != "fake":
        print(f"\n{config.HOST} 正在使用真的 OpenAI，停止壓測。請改用壓測環境（預設 http://localhost:8019，見 test/performance/README.md）。")
        environment.process_exit_code = 2
        environment.runner.quit()
