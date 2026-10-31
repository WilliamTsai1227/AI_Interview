# AI Interview

針對目標職缺反覆練習的 AI 語音模擬面試。規格書在 [spec/](spec/)：[architecture.md](spec/architecture.md)、[api.md](spec/api.md)、[postgresql.md](spec/postgresql.md)、[flows.md](spec/flows.md)，UI 原型是 [spec/AI Interview.html](spec/AI%20Interview.html)。每個產品功能的用途、用法和測試方式在 [spec/features/](spec/features/README.md)。

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

打開 <http://localhost:8009>，註冊一個帳號就能開始用。

- 會啟動 `db`、`redis`、`minio`、`migrate`（建表，跑完就結束）、`api`（同時提供前端）、`worker`。
- 要用 OpenAI 或 Google 登入：`cp backend/.env.example backend/.env` 後填金鑰，再 `docker compose up -d` 重新套用。沒有 `.env` 也能跑（開發模式）。每個變數的意思見下方〈環境變數〉。
- 本機開發想要現成的示範帳號（`demo@example.com` / `demo1234`，管理員）：`SEED_DEMO=true docker compose up -d --build`。正式環境不要開。
- 看 log：`docker compose logs -f api worker`；停止：`docker compose down`（加 `-v` 會一併刪除資料）。
- 改了程式碼要重新 build：`docker compose up -d --build api worker`。

## 環境變數

後端的設定都放在 `backend/.env`（範本是 [backend/.env.example](backend/.env.example)，複製一份再改）。**沒寫的變數會用程式裡的預設值**（[backend/app/core/config.py](backend/app/core/config.py)），所以 `.env` 只需要寫你想改的。

改完 `.env` 要讓容器重新讀取：

```bash
docker compose up -d api worker     # 只用 restart 不會讀到新的 .env
```

> **兩個容器都要重啟**：網頁和 API 跑在 `api`，但履歷解析、出題、評分、職涯分析這些背景工作跑在 `worker`。AI 相關的設定大多是 `worker` 在用。

> **用 Docker 跑時，有幾個變數 `.env` 寫了也沒用**：[docker-compose.yml](docker-compose.yml) 會直接指定 `DATABASE_URL`、`REDIS_URL`、`S3_ENDPOINT`、`S3_PUBLIC_ENDPOINT`、`S3_ACCESS_KEY`、`S3_SECRET_KEY`、`S3_BUCKET`、`BASE_URL`、`FRONTEND_DIR`，讓容器之間用服務名稱互連。要改這些，請改 `docker-compose.yml`。下表標「Docker 固定」的就是這些。

### 基本與網址

| 變數 | 預設值 | 白話說明 |
|---|---|---|
| `ENV` | `dev` | 執行環境。`dev`（開發）時限制比較寬鬆：每月面試次數不設上限，登入、職涯分析、建立面試的次數上限也放寬。**正式環境請改成 `prod`**（`dev` 以外的值都算正式環境）。 |
| `BASE_URL` | `http://localhost:8009` | 網站對外的網址。Google 登入的回呼網址會用它組出來，MinIO 也只允許這個網址的瀏覽器上傳檔案。正式環境要改成你的網域，例如 `https://interview.example.com`。（Docker 固定） |
| `FRONTEND_DIR` | `../frontend` | 前端檔案放在哪個資料夾。API 會順便提供這些網頁。一般不用改。（Docker 固定） |

### 資料庫與 Redis

| 變數 | 預設值 | 白話說明 |
|---|---|---|
| `DATABASE_URL` | `postgresql://ai:ai@localhost:5434/ai_interview` | PostgreSQL 的連線位址：帳號 `ai`、密碼 `ai`、資料庫 `ai_interview`。本機開發連 Docker 開出來的 5434 埠。（Docker 固定） |
| `DB_POOL_MIN` | `2` | 資料庫連線池最少保留幾條連線。 |
| `DB_POOL_MAX` | `10` | 資料庫連線池最多開幾條連線。同時使用的人很多、出現等連線的情況時再調高。 |
| `REDIS_URL` | `redis://localhost:6381/0` | Redis 的連線位址。Redis 負責背景工作佇列、即時通知、次數限制、短期快取。（Docker 固定） |

### 檔案儲存（S3／MinIO）

履歷和錄音不經過 API，由瀏覽器直接上傳到 S3 相容的儲存空間。開發時用本機的 MinIO，正式環境可以換成 AWS S3 或其他 S3 相容服務。

| 變數 | 預設值 | 白話說明 |
|---|---|---|
| `S3_ENDPOINT` | `http://localhost:9100` | 後端連 S3／MinIO 的位址。（Docker 固定，容器內用 `http://minio:9000`） |
| `S3_PUBLIC_ENDPOINT` | 空白（＝`S3_ENDPOINT`） | **瀏覽器**看到的 S3 位址。上傳和下載用的預簽名網址會用這個位址產生，所以必須是使用者的瀏覽器連得到的網址。安全標頭（CSP）也只放行這個來源。（Docker 固定） |
| `S3_REGION` | `us-east-1` | S3 區域。MinIO 隨便填都可以；AWS S3 要填 bucket 所在的區域。 |
| `S3_ACCESS_KEY` | `minioadmin` | S3 帳號（存取金鑰）。**正式環境一定要換掉。**（Docker 固定） |
| `S3_SECRET_KEY` | `minioadmin` | S3 密碼（私密金鑰）。**正式環境一定要換掉。**（Docker 固定） |
| `S3_BUCKET` | `ai-interview` | 檔案放在哪個 bucket（資料夾）。不存在時 API 啟動時會自動建立。（Docker 固定） |

### 登入與安全

| 變數 | 預設值 | 白話說明 |
|---|---|---|
| `JWT_SECRET` | `dev-secret-change-me` | 簽發登入憑證、面試語音網址簽章用的密鑰。**正式環境一定要換成一長串隨機字**（例如 `openssl rand -hex 32` 產生的值）。換掉後所有人都要重新登入。 |
| `ACCESS_TOKEN_MINUTES` | `15` | 登入憑證（access token）幾分鐘過期。過期後前端會自動用 refresh token 換新的，使用者不會感覺到。 |
| `REFRESH_TOKEN_DAYS` | `30` | 「保持登入」幾天。超過這個天數沒用，就要重新登入。 |
| `COOKIE_SECURE` | `false` | 登入用的 Cookie 是否只在 HTTPS 傳送。**正式環境（HTTPS）請設 `true`**；本機用 http 時保持 `false`，否則會登入不了。 |
| `GOOGLE_CLIENT_ID` | 空白 | Google 登入的用戶端 ID。跟下面的密鑰都有填，登入頁才會出現「使用 Google 繼續」。設定方式見下方〈Google 登入〉。 |
| `GOOGLE_CLIENT_SECRET` | 空白 | Google 登入的用戶端密鑰（`GOCSPX-` 開頭）。要跟上面的 Client ID 是同一組；對不起來時 Google 會回 `invalid_client`。 |
| `GOOGLE_REDIRECT_URI` | 空白（＝`BASE_URL` + `/api/v1/auth/google/callback`） | Google 登入完成後導回來的網址。一般留空即可，會自動用 `BASE_URL` 組出來。必須跟 Google Cloud 上填的「已授權的重新導向 URI」完全一樣。 |

### OpenAI 與 AI 模型

模型的選擇考量、每個模型用在哪些功能，請看下一節〈AI 模型與推理強度〉。

| 變數 | 預設值 | 白話說明 |
|---|---|---|
| `OPENAI_API_KEY` | 空白 | OpenAI 金鑰。**沒有填時整個網站改用本機假資料**（開發模式），所有流程都走得完，但題目、評分都是假的。 |
| `LLM_MODEL_REASONING` | `gpt-5` | 「需要想清楚」的工作用的模型：出題、評分、面試建議、職涯分析。 |
| `LLM_MODEL_FAST` | `gpt-5-mini` | 「要快、要便宜」的工作用的模型：面試中的追問、解析履歷和職缺、評分規準、報告總結、追加建議。 |
| `LLM_EFFORT_REASONING` | `low` | 上面 reasoning 模型的推理強度。 |
| `LLM_EFFORT_FAST` | `minimal` | 上面 fast 模型的推理強度。 |
| `LLM_TIMEOUT_REASONING` | `150` | reasoning 模型每次呼叫最多等幾秒，超過就算失敗（會重試）。 |
| `LLM_TIMEOUT_FAST` | `60` | fast 模型每次呼叫最多等幾秒。（面試中的追問另外有 3 秒上限，見 `FOLLOWUP_TIMEOUT_SEC`） |
| `STT_MODEL_FAST` | `gpt-4o-mini-transcribe` | 語音轉文字（快速版）。面試中使用者講完話時馬上轉一份文字，讓 AI 決定要不要追問。 |
| `STT_MODEL` | `gpt-4o-transcribe` | 語音轉文字（準確版）。每題回答完後在背景再轉一次，評分和報告都以這份為準。 |
| `TTS_MODEL` | `gpt-4o-mini-tts` | 文字轉語音：面試官 Ava 念題目、念追問的聲音。 |
| `TTS_VOICE` | `marin` | Ava 的聲音。官方建議最自然的是 `marin`（女聲）或 `cedar`（男聲）。換聲音後，舊的語音快取不會再被使用，會重新產生。 |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | 把履歷和職缺轉成「向量」的模型，目前只存進資料庫，給之後的職缺契合度計算用（尚未實作）。 |
| `REALTIME_MODEL` | `gpt-realtime-2.1` | 即時語音面試用的模型。**目前沒有使用**（即時語音尚未實作）。 |

### 面試與流量

| 變數 | 預設值 | 白話說明 |
|---|---|---|
| `ANSWER_MAX_SEC` | `300` | 每一題最多能回答幾秒（5 分鐘），時間到會自動結束錄音。 |
| `SILENCE_PROMPT_SEC` | `12` | 錄音中使用者安靜超過幾秒，Ava 會用語音提醒一次（例如請使用者開始回答）。 |
| `HEARTBEAT_SEC` | `15` | 面試中前端每幾秒回報一次「我還在」。超過 2 分鐘沒回報（例如關掉分頁、斷線），面試會自動暫停，之後可以繼續。 |
| `FOLLOWUP_TIMEOUT_SEC` | `3.0` | 面試中產生追問最多等幾秒。超過就改用備用追問（題庫裡預先寫好的追問，沒有的話用通用追問），不讓使用者乾等。 |
| `GROUP_WEIGHT_MAIN` | `0.6` | 一題的分數裡，主題目佔多少比重（0.6＝60%），其餘 40% 由追問平均。 |
| `MAX_LIVE_INTERVIEWS` | `200` | 全站同時進行中的面試最多幾場。滿了新的面試會請使用者稍候，避免 OpenAI 用量爆掉。 |
| `OPENAI_CONCURRENCY` | `32` | 每個模型同時最多送幾個請求給 OpenAI。 |
| `OPENAI_RESERVED_RATIO` | `0.3` | 上面的名額保留幾成給「面試中」的呼叫（追問、語音）。背景工作（出題、解析）最多只能用剩下的 7 成，確保正在面試的人不會因為背景工作太多而卡住。 |

### 上傳限制

| 變數 | 預設值 | 白話說明 |
|---|---|---|
| `RESUME_MAX_BYTES` | `10485760`（10 MB） | 履歷檔案大小上限。 |
| `AUDIO_MAX_BYTES` | `15728640`（15 MB） | 每段回答錄音的大小上限。 |
| `RESUME_MAX_PAGES` | `10` | PDF 履歷最多讀幾頁，超過的頁數會被忽略。 |

### 功能開關

| 變數 | 預設值 | 白話說明 |
|---|---|---|
| `FEATURE_JOB_SEARCH` | `false` | 職缺搜尋頁。第一版先關閉：側邊欄、選單、全站搜尋都看不到，搜尋 API 回 404。改成 `true` 並重啟 `api` 就會打開。 |

### Docker Compose 用的變數（不放在 `backend/.env`）

| 變數 | 預設值 | 白話說明 |
|---|---|---|
| `SEED_DEMO` | `false` | 啟動時要不要建立示範帳號 `demo@example.com` / `demo1234`（**管理員**）。只在本機開發時設 `true`；**正式環境不要開**，這組帳密寫在公開的程式碼裡，任何人都能用來登入管理員。用法：`SEED_DEMO=true docker compose up -d --build`，或寫在專案根目錄的 `.env`（注意：是根目錄，不是 `backend/.env`）。 |

### 職缺爬蟲排程（`data/scheduler/.env`）

爬蟲排程讀的是自己資料夾裡的 `.env`（範本 [data/scheduler/.env.example](data/scheduler/.env.example)），跟後端分開。目前沒有使用平台職缺，平常不需要設定。

| 變數 | 預設值 | 白話說明 |
|---|---|---|
| `DATABASE_URL` | `postgresql://ai:ai@localhost:5434/ai_interview` | 爬到的職缺要寫進哪個資料庫。 |
| `OPENAI_API_KEY` | 空白 | 有填才會用 AI 整理職缺摘要與重點。 |
| `CRAWLER_SUMMARIZE` | `true` | 是否用 AI 整理摘要（需要上面的金鑰）。 |
| `CRAWLER_SUMMARY_MODEL` | `gpt-5-mini` | 整理摘要用的模型。 |
| `CRAWLER_USER_AGENT` | 範例字串 | 爬蟲對網站自我介紹的名稱和聯絡方式，請改成你的網域和信箱。 |
| `CRAWLER_DELAY_SEC` | `2` | 每次抓網頁之間等幾秒，避免對網站造成負擔。 |
| `CRAWLER_MAX_PAGES_PER_SITE` | `60` | 每個公司網站最多抓幾頁。 |
| `CRAWLER_MAX_JOBS_PER_SITE` | `50` | 每個公司網站最多存幾筆職缺。 |
| `SOURCES_FILE` | 空白（＝`data/job_crawlers/sources/companies.json`） | 要爬的公司清單檔案。 |
| `CRAWL_AT` | `02:00` | 每天幾點自動爬，多個時間用逗號分隔，空白＝不排程。 |
| `VERIFY_AT` | `04:00` | 每天幾點檢查已存的職缺是否還在招募。 |
| `VERIFY_LIMIT` | `300` | 每次最多檢查幾筆職缺。 |
| `RUN_ON_START` | `false` | 啟動排程時是否先立刻爬一次。 |

## AI 模型與推理強度

### 為什麼分成兩種模型

AI 工作的需求不一樣：有的要快（使用者正在等），有的要準（影響評分可信度）。所以程式把文字模型分成兩「級」，每級可以各自換模型和推理強度：

| 級別 | 設定 | 預設模型 | 預設推理強度 | 選擇考量 |
|---|---|---|---|---|
| **fast**（快速） | `LLM_MODEL_FAST`、`LLM_EFFORT_FAST` | `gpt-5-mini` | `minimal` | 面試中的追問必須在 3 秒內回應，所以要小模型＋幾乎不思考。其他「照內容整理」的工作（解析履歷、職缺）也不需要深度推理，用便宜的小模型就夠。 |
| **reasoning**（推理） | `LLM_MODEL_REASONING`、`LLM_EFFORT_REASONING` | `gpt-5` | `low` | 出題、評分、面試建議直接影響使用者對產品的信任，要用能力較強的模型。這些都在背景執行，使用者可以等，所以慢一點沒關係；推理強度用 `low` 是在品質和速度之間取平衡。 |

### 每個功能用哪個模型

| 功能 | 什麼時候執行 | 級別 | 預設模型 | 使用者在等嗎 | 目前實測耗時 |
|---|---|---|---|---|---|
| 面試中的即時追問 | 使用者每回答完一題 | fast | `gpt-5-mini` | **是，正在面試** | 上限 3 秒 |
| 解析職缺 | 貼上職缺、按「依新內容重新出題」 | fast | `gpt-5-mini` | 間接（解析完才開始出題） | 約 5 秒 |
| 解析履歷 | 上傳履歷 | fast | `gpt-5-mini` | 否，背景 | 約 12 秒 |
| 評分規準 | 每題出好之後，替它準備評分標準 | fast | `gpt-5-mini` | 否，背景 | 約 11 秒 |
| 面試報告總結 | 每題評分完後，彙整整場的總評 | fast | `gpt-5-mini` | 等報告時 | 約 3 秒 |
| 追加建議 | 面試建議頁的可能題目／準備清單按「再多產生」 | fast | `gpt-5-mini` | **是** | 尚無紀錄 |
| 出題 | 加入目標職缺、題庫頁按「生成題目」、按「依新內容重新出題」 | reasoning | `gpt-5` | 否，背景 | 約 40 秒 |
| 回答評分 | 每題回答完後在背景評分（面試進行中就開始） | reasoning | `gpt-5` | 等報告時 | 約 11 秒／題 |
| 面試建議分析 | 加入目標職缺、換履歷、按「重新分析」 | reasoning | `gpt-5` | 否，背景 | 約 22 秒 |
| 職涯規劃分析 | 職涯規劃按「開始分析」 | reasoning | `gpt-5` | 是（畫面會顯示進度） | 約 15 秒 |

> 耗時來自資料庫 `llm_calls` 表的實際紀錄（2026 年 10 月，樣本數不多），僅供參考。每次 AI 呼叫的模型、耗時、token 數都會記在這張表。

語音和向量用的是專用模型，跟上面三級無關：

| 功能 | 設定 | 預設模型 | 考量 |
|---|---|---|---|
| Ava 念題目、念追問 | `TTS_MODEL`、`TTS_VOICE` | `gpt-4o-mini-tts`、`marin` | 可以用文字指示語氣（溫和、真實、嚴格三種面試官），聲音自然、速度快。追問用串流播放，邊產生邊念。 |
| 面試中快速轉文字 | `STT_MODEL_FAST` | `gpt-4o-mini-transcribe` | 要快，讓 AI 盡快決定要不要追問。 |
| 每題回答後準確轉文字 | `STT_MODEL` | `gpt-4o-transcribe` | 評分以這份為準，所以用較準的模型；在背景執行，慢一點沒關係。 |
| 履歷、職缺轉向量 | `EMBEDDING_MODEL` | `text-embedding-3-small` | 便宜。目前只存起來，給之後的契合度計算用。 |

### 推理強度是什麼

推理強度決定模型回答前「先想多久」。想越久通常越準，但越慢、越貴（思考也算 token 費用）。只有 GPT-5 系列這類推理模型才有這個設定。

| 強度 | 白話說明 | 速度與費用 | 適合 |
|---|---|---|---|
| `minimal` | 幾乎不思考，直接回答 | 最快、最便宜 | 照格式整理、抽資料、即時回應（追問） |
| `low` | 先想一小段再回答 | 快 | 需要一點判斷的工作：出題、評分、分析 |
| `medium` | 想得比較完整 | 中等，明顯變慢 | 需要多步驟推理的工作；本專案目前沒有用到 |
| `high` | 想很久 | 最慢、最貴 | 很難的推理問題；本專案目前沒有用到 |

**這三種寫法的結果不一樣，要特別注意：**

| `.env` 的寫法 | 結果 |
|---|---|
| 整行不寫（例如沒有 `LLM_EFFORT_FAST` 這行） | 用程式預設值：fast＝`minimal`、reasoning＝`low` |
| 寫了值（例如 `LLM_EFFORT_FAST=low`） | 用你寫的值 |
| 留空白（例如 `LLM_EFFORT_FAST=`） | **不送推理強度**，改用 OpenAI 模型自己的預設（GPT-5 系列是 `medium`），會明顯變慢、變貴。不建議。 |

### 調整推理強度會影響哪些功能

| 改這個設定 | 會影響 | 調高（例如 `minimal`→`low`） | 調低 |
|---|---|---|---|
| `LLM_EFFORT_FAST` | 即時追問、解析履歷、解析職缺、評分規準、報告總結、追加建議 | 解析比較不容易放錯欄位、追加建議比較不重複；但**追問會變慢**，容易超過 3 秒上限而改用備用追問（題目比較不貼合使用者剛才的回答） | 已經是最低（`minimal`） |
| `LLM_EFFORT_REASONING` | 出題、回答評分、面試建議、職涯規劃分析 | 題目更貼近職缺、評分理由更完整；但出題和報告要等更久、費用增加 | `minimal` 會更快更省，但題目和評分容易變得空泛 |

> 即時追問和其他 fast 功能共用 `LLM_EFFORT_FAST`，目前無法只調其中一個。如果想讓背景的解析用 `low`、追問維持 `minimal`，需要把設定拆開。

## 本機開發（API 在本機跑，可自動重載）

需要 Docker 與 Python 3.12+。

```bash
# 1. 只啟動基礎設施：PostgreSQL（5434）、Redis（6381）、MinIO（9100，主控台 9101）
docker compose up -d db redis minio

# 2. 後端環境
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env            # 要用真的 AI 就填 OPENAI_API_KEY

# 3. 建立資料表（想要示範帳號 demo@example.com / demo1234 再執行第二行）
.venv/bin/python -m app.core.migrate
.venv/bin/python -m app.seed --admin demo@example.com

# 4. 啟動 API（同時提供前端）與 worker（兩個終端機）
.venv/bin/uvicorn app.main:app --port 8009 --reload
.venv/bin/python -m app.workers.main
```

打開 <http://localhost:8009>。API 文件在 <http://localhost:8009/api/v1/docs>。

### Google 登入（選用）

1. Google Cloud Console → API 和服務 → OAuth 同意畫面：設定應用程式名稱、支援 Email；測試階段把自己的帳號加進「測試使用者」。
2. 憑證 → 建立憑證 → OAuth 用戶端 ID → 應用程式類型「網頁應用程式」。
3. 已授權的重新導向 URI 填 `http://localhost:8009/api/v1/auth/google/callback`（正式環境改成你的網域，並設定 `BASE_URL`、`COOKIE_SECURE=true`）。
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
spec/                規格書與 UI 原型；features/ 是每個產品功能的說明
```

## 尚未實作

- Realtime 語音（P3）：`realtime/connect` 目前固定回 `503 REALTIME_UNAVAILABLE`，前端自動使用標準語音。
- 職缺：已有公司官網爬蟲（[data/job_crawlers](data/job_crawlers/)），公司清單需自行填寫；104／CakeResume 需官方授權才能接。契合度計算（`compute_matches`）尚未實作。
- 方案付費。
