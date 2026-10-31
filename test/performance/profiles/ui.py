"""網頁介面手動測試：人數、加人速度在網頁上填，隨時可以改，看即時圖表。

  locust -f profiles/ui.py      → 打開 http://localhost:8089
load.py／stress.py 裡有固定的人數曲線（LoadTestShape），會蓋掉網頁上填的人數，所以手動測試要用這個檔。
瀏覽與面試使用者的比例是 4:1；不判定合格與否。
"""
from _common import BrowsingUser, InterviewUser  # noqa: F401（Locust 從這個檔案找使用者類別）
