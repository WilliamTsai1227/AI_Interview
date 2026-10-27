# 職缺爬蟲（AWS Lambda）

讀取**公司官網徵才頁**上提供給搜尋引擎的 [schema.org JobPosting](https://schema.org/JobPosting) 結構化資料（`<script type="application/ld+json">`），整理後寫進主系統 PostgreSQL 的 `job_posts`，網站的「職缺搜尋」頁就會出現這些職缺。

## 原則

| 原則 | 做法 |
|---|---|
| 只抓允許的內容 | 每個網站先讀 robots.txt；不允許的路徑不抓；連 robots.txt 都被擋就整個網站不抓 |
| 不繞過防護 | 遇到 Cloudflare 等機器人驗證、或 429，立即停止抓這個網站並記錄原因；**不使用無頭瀏覽器偽裝、驗證碼破解或代理輪換** |
| 禮貌 | 每個網站至少間隔 2 秒（robots.txt 有 Crawl-delay 就照它）、每站頁數上限、User-Agent 表明身分與聯絡方式；Lambda 同時只跑一個 |
| 原文只給 AI 用 | 網站顯示：職稱、公司、地點、薪資、雇用型態、刊登日期、原始網址、技術標籤；有 OpenAI 金鑰時另有 AI 以自己的話寫的摘要與重點。**原始職缺描述存在 `raw_text`，只給 Ava 出題與分析使用，主系統 API 不回傳、網站不顯示** |
| 一律導回原站 | 每筆都有 `source_url`，詳情頁顯示「前往原始職缺頁」 |
| 品質把關 | 分類不確定、地點不在台灣、已過截止日、沒有描述 → `pending_review`，進管理後台待審清單；其餘直接上架 |

> **104 人力銀行、CakeResume**：兩站的職缺頁都有 Cloudflare 機器人驗證，且使用條款限制自動擷取，所以不在這裡抓取。要使用這兩個來源，請洽談官方授權或合作 API，之後可以照「新增來源」的方式接上。

## 結構

```
job_crawlers/
├── handler.py            Lambda 進入點（mode: crawl／verify）
├── crawler/
│   ├── config.py         環境變數設定
│   ├── http.py           禮貌抓取：robots.txt、限速、身分、偵測機器人驗證即停止
│   ├── discover.py       找職缺頁：徵才頁上的連結＋robots.txt 宣告的 sitemap（只限同網域）
│   ├── jsonld.py         從 HTML 取出 JobPosting（支援 @graph、陣列）
│   ├── normalize.py      轉成 job_posts 欄位：城市、薪資、雇用型態、分類、資歷、標籤、審核判斷
│   ├── summarize.py      選用：OpenAI 寫摘要（不轉貼原文）
│   ├── db.py             寫入 PostgreSQL（pg8000，純 Python）：新增或更新、執行紀錄、下架
│   └── run.py            主流程（也可在本機執行）
├── sources/companies.json   要爬的公司清單
├── template.yaml         AWS SAM：Lambda＋每日排程
└── build.sh              打包成 build/ 與 job_crawler.zip
```

## 寫入資料庫的方式

| 欄位 | 值 |
|---|---|
| `source` / `source_site` | `catalog` / `company`（介面顯示「公司官網」） |
| `external_ref` | JobPosting 的 `identifier`，沒有就用網址雜湊 |
| 去重 | 正規化網址（去掉 utm 等追蹤參數）或 `(source_site, external_ref)`：已存在就更新事實欄位，**保留管理者的審核結果** |
| `job_ingest_runs` | 每次執行的抓取數、上架數、送審數、下架數、錯誤 |
| 下架 | verify 模式：原始頁 404／410、或 `validThrough` 已過 → `closed`；超過 30 天沒再抓到 → `closed` |

## 新增要爬的公司

編輯 `sources/companies.json`：

```json
{ "name": "acme", "company_name": "Acme 股份有限公司", "careers_url": "https://careers.acme.com/", "enabled": true }
```

先在本機試跑，看結果的 `issues`。最常見的是「沒有找到 schema.org JobPosting 結構化資料」，代表這家公司的官網沒有提供，就無法用這個方式取得。

## 本機執行

```bash
cd data/job_crawlers
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
docker compose -f ../../docker-compose.yml up -d db          # 主系統的資料庫

export DATABASE_URL=postgresql://ai:ai@localhost:5434/ai_interview
.venv/bin/python -m crawler.run crawl --limit 3              # 爬前 3 家公司
.venv/bin/python -m crawler.run verify                       # 檢查已上架職缺
```

## 部署到 AWS Lambda

```bash
./build.sh                       # 產生 build/ 與 job_crawler.zip
sam deploy --guided              # 依 template.yaml 建立 Lambda 與每日排程
```

- Runtime：Python 3.12（arm64）、記憶體 512 MB、逾時 15 分鐘；會在逾時前 60 秒停止並寫入執行紀錄，沒爬完的公司下次再抓。
- 排程：每天台灣時間 02:00 爬取、04:00 檢查。
- 網路：Lambda 要放在能連到 RDS 的私有子網路，並透過 NAT Gateway 連外網抓公司網站。
- 參數：`DatabaseUrl`（建議改用 Secrets Manager）、`OpenAIApiKey`（選填）、`CrawlerUserAgent`（請改成你的網域與聯絡信箱）。

| 環境變數 | 預設 | 說明 |
|---|---|---|
| `DATABASE_URL` | 本機 docker 的 db | PostgreSQL 連線字串 |
| `DATABASE_SSL` | — | `true` 時用 TLS（RDS 建議開啟） |
| `CRAWLER_USER_AGENT` | `AIInterviewJobBot/0.1 (…)` | 表明身分，請填真實聯絡方式 |
| `CRAWLER_DELAY_SEC` | `2` | 同一網站兩次請求的最短間隔 |
| `CRAWLER_MAX_PAGES_PER_SITE` / `CRAWLER_MAX_JOBS_PER_SITE` | `60` / `50` | 每站上限 |
| `OPENAI_API_KEY` | 空 | 有填才產生摘要；`CRAWLER_SUMMARIZE=false` 可關閉 |
| `CRAWLER_SUMMARY_MODEL` | `gpt-5-mini` | 摘要模型 |

Lambda 事件：`{"mode": "crawl"}`、`{"mode": "crawl", "companies": ["acme"]}`、`{"mode": "verify", "limit": 300}`。

上線前請確認各公司網站的使用條款允許這樣的取用方式。
