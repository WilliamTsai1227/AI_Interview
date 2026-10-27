"""設定一律來自環境變數（Lambda 的環境變數或本機 .env）。"""
import os
from dataclasses import dataclass, field


def _bool(v: str | None, default: bool = False) -> bool:
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Config:
    database_url: str = field(default_factory=lambda: os.environ.get(
        "DATABASE_URL", "postgresql://ai:ai@localhost:5434/ai_interview"))
    # 表明身分：網站管理者看得到是誰在抓、怎麼聯絡
    user_agent: str = field(default_factory=lambda: os.environ.get(
        "CRAWLER_USER_AGENT", "AIInterviewJobBot/0.1 (+https://example.com/bot; contact: bot@example.com)"))
    request_delay_sec: float = field(default_factory=lambda: float(os.environ.get("CRAWLER_DELAY_SEC", "2.0")))
    timeout_sec: float = field(default_factory=lambda: float(os.environ.get("CRAWLER_TIMEOUT_SEC", "15")))
    max_pages_per_site: int = field(default_factory=lambda: int(os.environ.get("CRAWLER_MAX_PAGES_PER_SITE", "60")))
    max_jobs_per_site: int = field(default_factory=lambda: int(os.environ.get("CRAWLER_MAX_JOBS_PER_SITE", "50")))
    # 選用：有 OpenAI 金鑰時，用 AI 寫 2–3 句摘要與重點條列（不轉貼原文）
    openai_api_key: str = field(default_factory=lambda: os.environ.get("OPENAI_API_KEY", ""))
    summary_model: str = field(default_factory=lambda: os.environ.get("CRAWLER_SUMMARY_MODEL", "gpt-5-mini"))
    summarize: bool = field(default_factory=lambda: _bool(os.environ.get("CRAWLER_SUMMARIZE"), True))
    # 只允許 https（測試用的本機伺服器可設 CRAWLER_ALLOW_HTTP=true）
    allow_http: bool = field(default_factory=lambda: _bool(os.environ.get("CRAWLER_ALLOW_HTTP")))

    @property
    def summarize_enabled(self) -> bool:
        return self.summarize and bool(self.openai_api_key)
