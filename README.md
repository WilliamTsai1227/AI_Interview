# AI Interview

針對目標職缺反覆練習的 AI 語音模擬面試。規格書在 [spec/](spec/)：[architecture.md](spec/architecture.md)、[api.md](spec/api.md)、[postgresql.md](spec/postgresql.md)、[flows.md](spec/flows.md)，UI 原型是 [spec/AI Interview.html](spec/AI%20Interview.html)。

| 部分 | 技術 |
|---|---|
| 前端 | 原生 HTML / CSS / JavaScript（ES Modules，無建置步驟）；畫面一律用 DOM API 建立，不使用 `innerHTML` |
| 後端 | FastAPI＋asyncpg，**手寫參數化 SQL**（不使用 ORM）；arq worker（Redis）＋PostgreSQL outbox |
| 資料 | PostgreSQL 16＋pgvector、Redis 7、S3 相容物件儲存（開發用 MinIO） |
| AI | OpenAI（文字 LLM、STT、TTS）；**沒有設定金鑰時使用本機假資料**，整個流程仍可走完 |

## 一行啟動（Docker）

```bash
docker compose up -d --build
```

打開 <http://localhost:8000>，示範帳號 `demo@example.com` / `demo1234`。

- 會啟動 `db`、`redis`、`minio`、`migrate`（建表＋範例資料，跑完就結束）、`api`（同時提供前端）、`worker`。
- 要用 OpenAI 或 Google 登入：`cp backend/.env.example backend/.env` 後填金鑰，再 `docker compose up -d` 重新套用。沒有 `.env` 也能跑（開發模式）。
- 不想放範例資料：`SEED_DEMO=false docker compose up -d --build`。
- 看 log：`docker compose logs -f api worker`；停止：`docker compose down`（加 `-v` 會一併刪除資料）。
- 改了程式碼要重新 build：`docker compose up -d --build api worker`。

## 本機開發（API 在本機跑，可自動重載）

需要 Docker 與 Python 3.12+。

```bash
# 1. 只啟動基礎設施：PostgreSQL（5434）、Redis（6381）、MinIO（9100，主控台 9101）
docker compose up -d db redis minio

# 2. 後端環境
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env            # 要用真的 AI 就填 OPENAI_API_KEY

# 3. 建立資料表與範例資料（範例職缺、示範帳號 demo@example.com / demo1234）
.venv/bin/python -m app.core.migrate
.venv/bin/python -m app.seed --admin demo@example.com

# 4. 啟動 API（同時提供前端）與 worker（兩個終端機）
.venv/bin/uvicorn app.main:app --port 8000 --reload
.venv/bin/python -m app.workers.main
```

打開 <http://localhost:8000>。API 文件在 <http://localhost:8000/api/v1/docs>。

### Google 登入（選用）

1. Google Cloud Console → API 和服務 → OAuth 同意畫面：設定應用程式名稱、支援 Email；測試階段把自己的帳號加進「測試使用者」。
2. 憑證 → 建立憑證 → OAuth 用戶端 ID → 應用程式類型「網頁應用程式」。
3. 已授權的重新導向 URI 填 `http://localhost:8000/api/v1/auth/google/callback`（正式環境改成你的網域，並設定 `BASE_URL`、`COOKIE_SECURE=true`）。
4. 把用戶端 ID 與密鑰填進 `backend/.env` 的 `GOOGLE_CLIENT_ID`、`GOOGLE_CLIENT_SECRET`，重啟 API。登入頁就會出現「使用 Google 繼續」。

### 開發模式（沒有 OpenAI 金鑰）

- 出題、面試建議、評分使用以職缺內容產生的範例資料。
- Ava 的語音改用瀏覽器語音合成；回答文字來自瀏覽器語音辨識（Chrome），沒有的話使用標示為「開發模式」的範例回答。
- 追問次數、狀態機、上傳、評分管線、報告與正式模式完全相同。

## 測試

```bash
cd backend
.venv/bin/python tests/e2e_api.py      # 對執行中的 API＋worker 走完整流程（含追問規則與整組分數）
.venv/bin/pytest -q                    # Google 登入（假的 Google 回應，不需要真的憑證）
```

## 目錄

```
backend/
  app/core/          設定、asyncpg 連線池、遷移 runner、Redis、物件儲存、安全、錯誤格式
  app/modules/       auth、account、uploads、resumes、targets、jobs、questions、preps、interviews、voice、reports、admin
  app/ai/            LLMClient（重試、用量控管、llm_calls）、各 Agent、文字工具
  app/workers/       outbox 投遞、arq worker、排程
  migrations/        純 SQL 遷移
frontend/
  index.html、css/app.css
  js/                dom.js（DOM 建構）、api.js、router.js、pages/、components/、interview/（錄音與播放）
data/job_crawlers/   職缺爬蟲（AWS Lambda）：公司官網 schema.org JobPosting → job_posts
data/scheduler/      在本機手動或定時執行爬蟲（讀資料夾內 .env）
spec/                規格書與 UI 原型
```

## 尚未實作

- Realtime 語音（P3）：`realtime/connect` 目前固定回 `503 REALTIME_UNAVAILABLE`，前端自動使用標準語音。
- 職缺：已有公司官網爬蟲（[data/job_crawlers](data/job_crawlers/)），公司清單需自行填寫；104／CakeResume 需官方授權才能接。契合度計算（`compute_matches`）尚未實作。
- 方案付費。
