"""AWS Lambda 進入點。

事件格式（EventBridge 排程或手動觸發）：
  {"mode": "crawl"}                                 爬 sources/companies.json 的所有公司
  {"mode": "crawl", "companies": ["acme"]}          只爬指定公司（name）
  {"mode": "crawl", "sources": [{...}]}             直接帶公司清單
  {"mode": "verify", "limit": 200}                  檢查已上架職缺是否還在
"""
import logging
import time

from crawler.config import Config
from crawler.run import crawl, load_sources, verify

logging.getLogger().setLevel(logging.INFO)


def lambda_handler(event, context):
    event = event or {}
    cfg = Config()
    # 留 60 秒給收尾（寫入執行紀錄），避免被 Lambda 逾時硬切
    remaining = context.get_remaining_time_in_millis() / 1000 if context else 900
    deadline = time.monotonic() + max(30, remaining - 60)
    if event.get("mode", "crawl") == "verify":
        return verify(cfg, int(event.get("limit", 200)), deadline)
    sources = load_sources(inline=event.get("sources"))
    if event.get("companies"):
        wanted = set(event["companies"])
        sources = [s for s in sources if s["name"] in wanted]
    return crawl(cfg, sources, deadline)
