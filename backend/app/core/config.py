"""所有設定由環境變數提供（.env），模型名稱、上限都不寫死在程式中。"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    env: str = "dev"
    base_url: str = "http://localhost:8009"
    frontend_dir: str = "../frontend"

    database_url: str = "postgresql://ai:ai@localhost:5434/ai_interview"
    db_pool_min: int = 2
    db_pool_max: int = 10
    redis_url: str = "redis://localhost:6381/0"

    s3_endpoint: str = "http://localhost:9100"
    s3_public_endpoint: str = ""          # 瀏覽器看到的位址；空白時同 s3_endpoint
    s3_region: str = "us-east-1"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_bucket: str = "ai-interview"

    jwt_secret: str = "dev-secret-change-me"
    access_token_minutes: int = 15
    refresh_token_days: int = 30
    cookie_secure: bool = False           # 正式環境（HTTPS）設為 true

    # Google 登入（OAuth 2.0 授權碼＋PKCE）；沒有設定時登入頁不顯示 Google 按鈕
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = ""         # 空白時 = base_url + /api/v1/auth/google/callback

    # 管理後台（/#admin）：三個條件都要符合才能進
    #   1. 帳號 Email 在 ADMIN_GOOGLE_EMAILS（逗號分隔）  2. 這次是用 Google 登入  3. 輸入後台帳號密碼
    # 三個值任一沒設定 → 後台整個關閉
    admin_google_emails: str = ""
    admin_console_username: str = ""
    admin_console_password: str = ""
    admin_console_minutes: int = 30       # 輸入後台密碼後可以用多久（之後要重新輸入）

    # OpenAI：沒有金鑰時改用本機假資料（開發用），整個流程仍可走完
    openai_api_key: str = ""
    llm_model_reasoning: str = "gpt-5"
    llm_model_fast: str = "gpt-5-mini"
    # 推理強度（只對支援的推理模型送出；空白＝不送）。追問要在 3 秒內回應，所以 fast 用 minimal
    llm_effort_reasoning: str = "low"
    llm_effort_fast: str = "minimal"
    llm_timeout_reasoning: float = 150
    llm_timeout_fast: float = 60
    stt_model_fast: str = "gpt-4o-mini-transcribe"
    stt_model: str = "gpt-4o-transcribe"
    tts_model: str = "gpt-4o-mini-tts"
    tts_voice: str = "marin"              # 官方建議最自然的是 marin（女聲）或 cedar（男聲）
    embedding_model: str = "text-embedding-3-small"
    realtime_model: str = "gpt-realtime-2.1"

    # 面試
    answer_max_sec: int = 300
    silence_prompt_sec: int = 12
    heartbeat_sec: int = 15
    followup_timeout_sec: float = 3.0
    group_weight_main: float = 0.6
    max_live_interviews: int = 200        # 准入控制門檻（§9.4）
    openai_concurrency: int = 32          # 每個模型的同時呼叫上限
    openai_reserved_ratio: float = 0.3    # 保留給面試中呼叫的比例

    # 職涯規劃讀取作品集（GitHub）：沒有 token 時 GitHub API 每個 IP 每小時 60 次，有 token 5000 次
    github_token: str = ""
    portfolio_max_repos: int = 5          # 一次分析最多深入讀幾個 repo
    portfolio_repo_chars: int = 120_000   # 每個 repo 最多讀多少字的設定檔＋原始碼（不含 README）

    # 上傳
    resume_max_count: int = 3             # 每個帳號最多保留幾份履歷（每份解析一次，給出題、評分、職涯規劃共用）
    resume_max_bytes: int = 10 * 1024 * 1024
    audio_max_bytes: int = 15 * 1024 * 1024
    resume_max_pages: int = 10

    # 只給效能測試用：所有壓測使用者都從同一個 IP 發出，次數限制會先擋住測試。只有 ENV=dev 時才有效，正式環境設了也不會關
    rate_limits_enabled: bool = True

    # 功能開關（正式環境第一版先關閉，之後要開把環境變數改成 true 並重啟即可）
    feature_job_search: bool = False      # 職缺搜尋頁；關閉時職缺詳情（職涯規劃、面試建議會用到）照常運作

    @property
    def ai_enabled(self) -> bool:
        return bool(self.openai_api_key)

    @property
    def admin_emails(self) -> set[str]:
        return {e.strip().lower() for e in self.admin_google_emails.split(",") if e.strip()}

    @property
    def admin_console_enabled(self) -> bool:
        return bool(self.admin_emails and self.admin_console_username and self.admin_console_password)

    @property
    def google_enabled(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)

    @property
    def google_callback(self) -> str:
        return self.google_redirect_uri or f"{self.base_url.rstrip('/')}/api/v1/auth/google/callback"

    @property
    def is_dev(self) -> bool:
        return self.env == "dev"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
