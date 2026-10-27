# 本機職缺爬蟲排程

在你的主機上執行 [../job_crawlers](../job_crawlers/) 的爬蟲：讀這個資料夾的 `.env`，可以手動下指令立刻爬，也可以常駐每天定時爬。結果寫進根目錄 `docker compose` 起來的 PostgreSQL，網站「職缺搜尋」頁就會出現（來源顯示「公司官網」）。

## 第一次使用

```bash
# 1. 根目錄先把網站與資料庫跑起來
cd /Users/william/Documents/project/AI_Interview
docker compose up -d --build

# 2. 設定（.env 已有預設值，至少確認公司清單）
cd data/scheduler
cp -n .env.example .env          # 已經有 .env 就不會覆蓋
#    編輯 ../job_crawlers/sources/companies.json，加入公司並設 "enabled": true
```

## 指令

| 指令 | 做什麼 |
|---|---|
| `./run.sh crawl` | 立刻爬一次（所有 enabled 的公司） |
| `./run.sh crawl acme` | 只爬指定公司（companies.json 的 `name`） |
| `./run.sh verify` | 立刻檢查已上架職缺：原始頁 404／過期 → 下架 |
| `./run.sh status` | 最近 10 次執行紀錄、職缺數量、下一次排程時間 |
| `./run.sh start` | 常駐排程：每天依 `CRAWL_AT`／`VERIFY_AT` 自動執行，Ctrl+C 停止 |

第一次執行會自動建立 `.venv` 並安裝依賴。手動指令與排程共用一把鎖，同時間只會有一個爬蟲在跑。

想讓排程在背景執行、關掉終端機也不停：

```bash
nohup ./run.sh start > /dev/null 2>&1 &     # 停止：pkill -f "scheduler.py start"
```

## .env 設定

| 變數 | 預設 | 說明 |
|---|---|---|
| `DATABASE_URL` | `postgresql://ai:ai@localhost:5434/ai_interview` | docker compose 的資料庫，通常不用改 |
| `OPENAI_API_KEY` | 空 | 選填；有填才用 AI 寫職缺摘要與重點（可複製 `backend/.env` 的金鑰） |
| `CRAWLER_SUMMARIZE` / `CRAWLER_SUMMARY_MODEL` | `true` / `gpt-5-mini` | 摘要開關與模型 |
| `CRAWLER_USER_AGENT` | `AIInterviewJobBot/0.1 (…)` | 表明身分，請改成你的網域與信箱 |
| `CRAWLER_DELAY_SEC` | `2` | 同一網站兩次請求的最短間隔 |
| `CRAWLER_MAX_PAGES_PER_SITE` / `CRAWLER_MAX_JOBS_PER_SITE` | `60` / `50` | 每站上限 |
| `SOURCES_FILE` | 空 | 公司清單檔；空白 = `../job_crawlers/sources/companies.json` |
| `CRAWL_AT` / `VERIFY_AT` | `02:00` / `04:00` | 每天執行時間（24 小時制，可用逗號設多個；空白 = 不排程） |
| `VERIFY_LIMIT` | `300` | 每次最多檢查幾筆 |
| `RUN_ON_START` | `false` | 啟動排程時先立刻爬一次 |

已經用 `export` 設定的變數優先於 `.env`，方便臨時調整，例如 `CRAWLER_DELAY_SEC=5 ./run.sh crawl`。

## 結果存在哪

| 資料表 | 內容 |
|---|---|
| `job_posts` | 職缺（`source_site = 'company'`）：職稱、公司、地點、薪資、雇用型態、標籤、分類、原始網址；有 OpenAI 金鑰時另有摘要與重點；完整原文在 `raw_text`（只給 AI 出題用，網站不顯示） |
| `job_ingest_runs` | 每次執行的統計，`details` 欄位是明細：每家公司抓到幾筆、新增／更新幾筆、遇到的問題 |

log 寫在 `logs/scheduler.log`（自動輪替）。
