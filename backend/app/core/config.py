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

    # OpenAI：沒有金鑰時改用本機假資料（開發用），整個流程仍可走完
    openai_api_key: str = ""
    llm_model_reasoning: str = "gpt-5"
    llm_model_fast: str = "gpt-5-mini"
    # 推理強度（只對支援的推理模型送出；空白＝不送）。追問要在 3 秒內回應，所以 fast 用 minimal
    llm_effort_reasoning: str = "low"
    llm_effort_fast: str = "minimal"
    llm_timeout_reasoning: float = 150
    # 網路搜尋（職涯規劃找職缺）：官方建議用較強的模型並提高推理強度；minimal 不支援 web_search
    llm_model_search: str = "gpt-5.5"
    llm_effort_search: str = "medium"
    llm_timeout_search: float = 240
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

    # 上傳
    resume_max_bytes: int = 10 * 1024 * 1024
    audio_max_bytes: int = 15 * 1024 * 1024
    resume_max_pages: int = 10

    # 功能開關（正式環境第一版先關閉，之後要開把環境變數改成 true 並重啟即可）
    feature_job_search: bool = False      # 職缺搜尋頁；關閉時職缺詳情（職涯規劃、面試建議會用到）照常運作

    @property
    def ai_enabled(self) -> bool:
        return bool(self.openai_api_key)

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
