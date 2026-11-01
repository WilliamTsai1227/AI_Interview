# 效能測試（Locust）

用 [Locust](https://locust.io/)（Python）模擬很多使用者同時使用網站，量 API 的速度、錯誤率，以及系統能撐多少人。

## 測試類型

| 類型 | 做法 | 回答什麼問題 | 腳本 |
|---|---|---|---|
| 冒煙測試 | 2 人跑 1 分鐘 | 腳本和環境正不正常（每次正式測試前先跑） | `profiles/smoke.py` |
| 負載測試 | 預期的正常人數（預設 50 人）跑 10 分鐘 | 平常的流量下，速度和錯誤率合不合格 | `profiles/load.py` |
| 壓力測試 | 每分鐘加 25 人，加到 300 人，再降回 10 人 | 極限在哪、壞掉時怎麼壞、流量退了能不能恢復 | `profiles/stress.py` |

尖峰測試（瞬間暴增）、耐久測試（跑好幾個小時）之後要加，就在 `profiles/` 新增一個檔案。

## 資料夾結構

一次壓測要回答三個問題，每個資料夾負責一個：

| 資料夾 | 回答的問題 | 比喻 |
|---|---|---|
| `scenarios/` | 使用者**做什麼**：打哪些 API、照什麼順序 | 劇本 |
| `profiles/` | **幾個人**、**跑多久**、怎樣算**合格** | 排練表：今天幾個演員上台、演多久 |
| `lib/` | 上面兩者都會用到的共用工具 | 道具間 |

拆開的好處是劇本只寫一次，可以搭配不同的人數設定：冒煙、負載、壓力、網頁手動，四種測試跑的都是同一份劇本，差別只在人數和時間。以後新增一種測試，例如耐久測試，只要在 `profiles/` 加一個檔，不用重寫劇本。

**`scenarios/`：測試項目（打哪些 API）寫在這裡**

| 檔案 | 內容 |
|---|---|
| [browse.py](scenarios/browse.py) | 瀏覽型使用者：登入後在各頁面之間看資料 |
| [interview.py](scenarios/interview.py) | 面試型使用者：完整走一場面試 |

**`profiles/`：人數、時間、合格標準**

| 檔案 | 內容 |
|---|---|
| [_common.py](profiles/_common.py) | 每個 profile 都會引用它。負責把兩種使用者載進來，並在開始前檢查目標環境是假 AI，不是就停止 |
| [smoke.py](profiles/smoke.py) | 每種使用者各 1 人，跑 1 分鐘，一個錯誤都不能有 |
| [load.py](profiles/load.py) | 50 人、60 秒內加滿、跑 10 分鐘，並列出合格標準（p95、失敗率） |
| [stress.py](profiles/stress.py) | 每分鐘加 25 人，加到 300 人，再降回 10 人看能不能恢復 |
| [ui.py](profiles/ui.py) | 只載入兩種使用者，人數由你在網頁上填 |

**`lib/`：共用工具**

| 檔案 | 內容 |
|---|---|
| [config.py](lib/config.py) | 所有設定集中在這：網址、測試帳號密碼、帳號數量、每題講幾秒，都可以用環境變數改 |
| [accounts.py](lib/accounts.py) | 分配帳號：每個虛擬使用者拿不同的帳號（perf-0000、perf-0001……），避免搶同一個帳號 |
| [api.py](lib/api.py) | 呼叫 API 的包裝：自動帶登入憑證、憑證過期自動重新登入、狀態碼不對就記一筆失敗；也負責等背景工作完成並記錄花了多久 |
| [checks.py](lib/checks.py) | 測試結束時對照合格標準，不合格就讓結束碼變成 1 |
| [steps.py](lib/steps.py) | 壓力測試專用：每 5 秒記一次數據，最後印出每一階的結果和「開始撐不住的人數」 |

**其他檔案**

| 檔案 | 內容 |
|---|---|
| [prepare.py](prepare.py) | 測試前跑一次：建 300 個測試帳號，每個帳號貼一個目標職缺並等題目產生 |
| [docker-compose.perf.yml](docker-compose.perf.yml) | 壓測環境的差異設定 |
| `requirements.txt` | 要安裝的套件，只有 Locust |

## 模擬的使用者（測試項目）

每個 profile 都會從 `_common.py` 載入 `BrowsingUser` 和 `InterviewUser`。Locust 看到這兩個使用者類別，就會照 4:1 的比例產生虛擬使用者，去跑 `scenarios/` 裡寫好的流程。所以**測試項目寫在 `scenarios/` 的兩個檔案裡**。

**瀏覽型（[browse.py](scenarios/browse.py)）**

每個 `@task(數字)` 是一個動作，數字是被抽中的機率權重，每做完一個動作停 2–6 秒：

| 權重 | 動作 | 打的 API |
|---|---|---|
| 一開始 | 登入、拿目標職缺 | `POST /auth/login`、`GET /targets` |
| 4 | 首頁 | `GET /me`、`GET /targets` |
| 3 | 目標職缺詳情 | `GET /targets/[id]` |
| 3 | 題庫 | `GET /targets/[id]/questions` |
| 2 | 面試建議 | `GET /targets/[id]/prep` |
| 2 | 報告 | `GET /interviews`、`GET /interviews/[id]/report` |
| 1 | 職涯規劃 | `GET /career/profile`、`GET /career/plans/latest` |

**面試型（[interview.py](scenarios/interview.py)）**

照順序走完一整場快速面試（3 題＋追問），結束後休息 5–15 秒再開下一場：

1. 建立面試：`POST /interviews`
2. 每幾秒查一次 `GET /interviews/[id]`，直到面試準備好。這段等待時間記成 `[async] 面試準備好`
3. 開始：`POST /interviews/[id]/start`
4. 每一題，含追問：
   - 題目播完：`.../playback-finished`
   - 開始回答：`.../answers`
   - 模擬講話約 5 秒
   - 申請上傳網址：`POST /uploads`
   - 把錄音直接傳到 MinIO：`PUT [minio] 錄音檔`
   - 送出這題：`.../attempts/[id]/complete`
5. 面試結束後，每幾秒查一次 `GET /interviews/[id]/report`，直到報告完成。這段等待時間記成 `[async] 報告產生`

兩個 `[async]` 項目量的是背景工作的完成時間，不是單一 API 的速度，在統計表裡類型是 `ASYNC`。Locust 網頁的 **Statistics** 分頁和報表裡，每一列就是上面其中一個名稱，可以直接對照。

**沒有測到的：** 上傳履歷、貼新的目標職缺、產生職涯規劃這些「寫入、而且會觸發 AI」的功能。在真實流量裡它們不常發生，而 AI 在壓測環境又是假的，所以先沒放進劇本。要加的話，就是在 `scenarios/` 加一個 `@task` 或一個新的使用者類別。

## 怎麼跑

需要 Docker 和 Python 3.12 以上。

**1. 啟動壓測環境**（在專案根目錄執行）

```bash
docker compose -p ai_perf -f docker-compose.yml -f test/performance/docker-compose.perf.yml up -d --build
```

會另外起一整套 api、worker、db、redis、minio，網址 <http://localhost:8019>，跟開發環境互不影響（見下一節）。

**2. 安裝 Locust、建立測試資料**（在 `test/performance` 執行；安裝只要第一次）

```bash
cd test/performance
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python prepare.py        # 建立 300 個測試帳號（perf-0000@example.com…），每個有一個目標職缺
```

**3. 跑測試**（同一個資料夾，`.venv` 要啟用）

```bash
mkdir -p results
locust -f profiles/smoke.py  --headless --only-summary --html results/smoke.html  --csv results/smoke    # 先確認一切正常
locust -f profiles/load.py   --headless --only-summary --html results/load.html   --csv results/load     # 負載測試
locust -f profiles/stress.py --headless --only-summary --html results/stress.html --csv results/stress   # 壓力測試
```

- `--headless`：不開網頁介面，跑完自動結束。
- `--html` / `--csv`：報表存到哪裡。同一個檔名會被覆蓋，想保留舊結果就換檔名。
- 想用網頁介面手動調人數、看即時圖表：`locust -f profiles/ui.py`，打開 <http://localhost:8089>，填人數和加人速度後按 START，跑的中途也能按 EDIT 改人數。
  - 不要用 `load.py`、`stress.py` 開網頁介面：它們有寫死的人數曲線，網頁上填的人數會被忽略。
  - 網頁介面不會檢查合格標準，要判定合格與否請用上面的 `--headless` 指令。

網頁介面的欄位（Run time、Profile 在 Advanced options 裡）：

| 欄位 | 意思 | 建議 |
|---|---|---|
| Number of users | 最多同時幾個虛擬使用者。不是每秒幾個請求：每個人做完一個動作會停幾秒，實際每秒請求數看右上角 RPS | 5 人以上才會有面試型使用者（瀏覽:面試 = 4:1，例如 50 人約 40 瀏覽、10 面試） |
| Ramp up | 每秒新增幾個人，直到加滿。例如 50 人、每秒 5 人，約 10 秒加滿 | 1～5。填太大會讓所有人同時登入，測到的主要是登入尖峰 |
| Host | 要打的網址 | 維持 `http://localhost:8019`（壓測環境），不要改成 8009 |
| Run time | 跑多久後自動停止，例如 `20s`、`10m`、`1h20m`；從按 START 開始算，包含加人的時間 | 留空就一直跑到按 STOP；想讓每次結果好比較就填固定值 |
| Profile | 這次測試的標籤文字，會寫進 HTML 報表，方便分辨是哪次測的（例如 `50人-修正前`）。跟本資料夾的 `profiles/` 無關，不影響測試行為 | 可以留空 |

**4. 用完關掉**（在專案根目錄執行）

```bash
docker compose -p ai_perf -f docker-compose.yml -f test/performance/docker-compose.perf.yml down -v
```

`-v` 會一起刪掉壓測環境的資料庫和檔案；只想暫停、下次接著用就拿掉 `-v`（下次不用再跑 `prepare.py`）。

### 一定要帶 `-p ai_perf`

`-p` 是專案名稱，Docker 靠它分辨是哪一套環境。不帶的話，預設是資料夾名稱 `ai_interview`，也就是開發環境。

之後對壓測環境下的每一個指令都要帶同樣的 `-p ai_perf`，否則操作到的是開發環境：

```bash
docker compose -p ai_perf -f docker-compose.yml -f test/performance/docker-compose.perf.yml ps        # 看壓測環境
docker compose -p ai_perf -f docker-compose.yml -f test/performance/docker-compose.perf.yml logs api  # 看壓測 api 的 log
docker compose -p ai_perf -f docker-compose.yml -f test/performance/docker-compose.perf.yml down -v   # 刪壓測環境
```

最危險的是 `down -v`：漏了 `-p ai_perf`，刪掉的就是你的開發資料庫。

## 壓測環境跟開發環境的差別

壓測用另一套獨立的 Docker 環境（[docker-compose.perf.yml](docker-compose.perf.yml)，專案名稱 `ai_perf`）：同一份程式，但連接埠、資料庫、檔案儲存都分開，**不會動到開發資料**。

| | 開發環境 | 壓測環境 |
|---|---|---|
| 網址 | <http://localhost:8009> | <http://localhost:8019> |
| AI | 真的 OpenAI | **假資料**：測的是我們自己的 API、資料庫、Redis、背景佇列；不花錢，也不會被 OpenAI 限流 |
| 次數限制 | 開 | **關**（`RATE_LIMITS_ENABLED=false`）：所有壓測使用者都從同一台電腦發出，不關會先被擋 |
| 登入憑證 | 15 分鐘過期 | 4 小時（長時間測試不會中途失效） |

腳本開始前會檢查目標環境：如果在用真的 OpenAI，會直接停止，避免不小心壓到開發環境燒錢。

> 因為 AI 是假的，測出來的是「AI 以外」的效能。真實情況下，出題、評分、追問還要加上 OpenAI 的回應時間（見根目錄 README〈AI 模型與推理強度〉）。

> 壓測環境跟開發環境跑在同一台電腦時會互搶 CPU，要比較準的數字，先停掉開發環境（根目錄 `docker compose stop`）。

## 合格標準

負載測試結束時自動檢查，不合格的話 Locust 結束碼是 1（可以放進 CI）。標準寫在 [profiles/load.py](profiles/load.py)：

| 項目 | 標準 |
|---|---|
| 整體失敗率 | ≤ 1% |
| 整體 p95（所有 API，不含背景工作） | ≤ 800 ms |
| 目標職缺、題庫、報告頁 | p95 ≤ 500 ms |
| 面試中送出回答 | p95 ≤ 1 秒 |
| 面試準備好 | p95 ≤ 10 秒 |
| 報告產生 | p95 ≤ 30 秒 |

p95 的意思：95% 的請求在這個時間內完成，只有最慢的 5% 超過。

壓力測試不判定通過或失敗，而是印出每一階的結果，並指出「開始撐不住的人數」（第一個 p95 超過 800 ms 或失敗率超過 1% 的階段）。

## 看結果

每次測試在 `results/` 產生：
- `*.html`：Locust 報表，有每支 API 的數據和隨時間變化的圖表。
- `*_stats.csv`、`*_stats_history.csv`、`*_failures.csv`：原始數據。
- `stress-steps-*.csv`：壓力測試每 5 秒的紀錄。

## 可以調整的設定

用環境變數，例如 `PERF_USERS=100 PERF_DURATION=1800 locust -f profiles/load.py --headless`。

| 變數 | 預設 | 說明 |
|---|---|---|
| `PERF_HOST` | `http://localhost:8019` | 要壓測的網址 |
| `PERF_ACCOUNTS` | `300` | 測試帳號數量，要 ≥ 最多同時在線的人數（同一帳號同時只能有一場面試）；`prepare` 和測試要用同一個值 |
| `PERF_ANSWER_SEC` | `5` | 模擬每題講幾秒（真人約 60 秒，壓測時縮短） |
| `PERF_ASYNC_TIMEOUT` | `120` | 等背景工作最多幾秒，超過算失敗 |
| `PERF_USERS` / `PERF_RAMP` / `PERF_DURATION` | `50` / `60` / `600` | 負載測試：人數、幾秒內加到滿、滿人數維持幾秒（冒煙測試的 `PERF_DURATION` 預設 60） |
| `PERF_STEP_USERS` / `PERF_STEP_SEC` / `PERF_MAX_USERS` | `25` / `60` / `300` | 壓力測試：每階加幾人、每階幾秒、最多幾人 |

## 第一次測試結果（2026-10-31，MacBook，Docker 4 核心 8 GB）

| 測試 | 結果 |
|---|---|
| 冒煙 | 0 錯誤，完整面試流程正常 |
| 負載 50 人 × 3 分鐘 | 0 錯誤；整體 p95 12 ms；送出回答 p95 17 ms；報告 1.5 秒產生 |
| 壓力 50→300 人（每階 30 秒） | 300 人內 0 錯誤；300 人時每秒約 134 個請求；報告 p95 2.5 秒 |

測試過程發現並修正的問題：
1. **偶發的連線失敗**：uvicorn 閒置連線預設 5 秒就關，使用者剛好在那一刻送請求就會失敗。改成 75 秒（也要比正式環境前面的負載平衡器長，否則會有偶發 502）。
2. **登入尖峰卡住所有 API**：密碼驗證（argon2）直接在事件迴圈裡算，大量登入時其他 API 最慢被拖到 400 ms。改到執行緒裡算、並移出資料庫交易後，其他 API 最慢降到約 100–165 ms，報告產生 p95 從 5.6 秒降到 2.5 秒。

還沒處理的觀察：
- **登入本身在尖峰時仍需約 300 ms**：argon2 預設每次驗證約 36 ms、用 4 條執行緒和 64 MB 記憶體，4 核心大約只能每秒處理十幾個登入。要改善可以加 CPU、開多個 API 程序（uvicorn `--workers`），或把 argon2 參數降到 OWASP 建議的最低標準（較快，但密碼保護較弱）。
- **報告產生時間隨人數變長**：背景工作在排隊（worker 同時處理的數量有上限），流量更大時要加開 worker。
