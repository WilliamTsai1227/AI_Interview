---
name: notion-test
description: 執行專案測試（pytest、e2e、效能、部署演練）或整理剛完成的除錯過程，並把結果記錄到 Notion「系統測試」底下對應 inline 資料庫的「測試項目」裡
argument-hint: [要跑的測試（unit／e2e／perf／drill）或要記錄的問題]
disable-model-invocation: true
---

執行測試或整理除錯過程，記錄到 Notion「系統測試」。

使用者補充說明：$ARGUMENTS

## 先讀
- `.claude/notion-workspace.md`：位置、資料庫快取、共用規則（一定要照著做，特別是**不使用任何表情符號**）。
- 系統測試頁面：https://app.notion.com/p/4192dbee5080805e9df8e02743f4b837

## Notion 結構（三層）

```txt
系統測試（頁面）
 ├─ API（inline 資料庫）            一個 API 種類一列，例：Auth、Interviews、Admin
 ├─ 資安測試（inline 資料庫）        一種檢測類別一列，例：管理後台存取控制、Google 登入、限流
 ├─ 附載測試（inline 資料庫）        一種效能情境一列，例：smoke、load、stress
 └─ 整合測試？（UI)（inline 資料庫）  一條端到端流程一列，例：e2e 完整流程、部署演練
      └─ 每一列的頁面裡都有一個 inline 資料庫「測試項目」
           └─ 每一列是一個測試案例或一次 bug 紀錄
```

- **第二層頁面**（例：`Interviews`）：開頭寫測什麼、測試檔案位置、怎麼跑，最後是 inline 資料庫「測試項目」。
- **「測試項目」欄位**（參考 Stock-Insight-chat 的系統測試）：
  - `Request`（標題）：`[通過] YYYY-MM-DD 測試名稱`；失敗用 `[失敗]`，修好前的 bug 用 `[未修正]`
  - `簡述`（文字）：一句話結果
  - `BUG程度`（select）：緊急／中等／輕微／純測試（沒有 bug 的一般測試用「純測試」）
  - `結果`（select）：通過／失敗／未執行
  - `測試檔案`（文字）：例 `backend/tests/test_admin_console.py::test_full_gate_passes`
- 第二層頁面還沒有「測試項目」時，用 `notion-create-database` 建在該頁面底下，欄位照上面。

## 1. 執行測試（使用者要求跑測試時）

這個 repo 沒有 `backend/.venv`，用 docker 跑：

| 說法 | 怎麼跑 | 前置條件 |
|---|---|---|
| unit／單元測試 | `docker compose build api` → `docker compose up -d db redis minio` → `docker compose run --rm migrate` → `docker compose run --rm --no-deps -e OPENAI_API_KEY= -e ENV=dev -e GOOGLE_CLIENT_ID= -e GOOGLE_CLIENT_SECRET= api python -m pytest -q -rA -p no:cacheprovider tests/` | Docker |
| e2e | 用假 AI 啟動：`docker compose run -d --name e2e-api --service-ports -e OPENAI_API_KEY= -e ENV=dev -e RATE_LIMITS_ENABLED=false -e FEATURE_JOB_SEARCH=true api`、`docker compose run -d --name e2e-worker -e OPENAI_API_KEY= -e ENV=dev worker`；在 scratchpad 建 venv 裝 `httpx python-docx`，跑 `python -I backend/tests/e2e_api.py http://localhost:8009` | 8009 埠沒被 k3s 佔用 |
| drill／部署演練 | 見 `deploy/k3s/README.md`〈演練〉 | k3s 在跑；**會部署，先問使用者** |
| perf／效能 | 見 `test/performance/README.md` | 獨立壓測環境；**先問使用者** |

- **一律清空 `OPENAI_API_KEY`**，除非使用者要求用真的 AI（會花錢）。
- 跑完把自己啟動的容器停掉（`docker rm -f e2e-api e2e-worker`、`docker compose stop`），不要 `down -v`、不要刪資料。
- 記下：指令、環境、commit（`git rev-parse --short HEAD`，有未提交修改要註明）、通過／失敗數、失敗的測試名稱與錯誤摘要。
- 失敗時先排查原因；要改程式前先問使用者。

## 2. 寫入 Notion
1. fetch 系統測試頁面與 4 個資料庫，依性質選資料庫與第二層頁面：
   - 測某個 API 種類的行為 → API（種類名稱與「系統設計 / API」一致）
   - 權限、登入、注入、限流、檔案驗證 → 資安測試（同一個測試可以同時記在 API 與資安）
   - 效能 → 附載測試
   - 跨多個 API 的完整流程、部署演練、瀏覽器操作 → 整合測試
   - 分不出來先問使用者
2. 第二層頁面不存在就新增一列（不給 icon），並在頁面裡建「測試項目」資料庫。
3. 每個測試案例一列；同一個案例已存在就更新結果與日期，不要重複新增。
4. 測試項目頁面內文：
   - **測試範圍**：測什麼、為什麼
   - **步驟／請求**：指令或 HTTP 請求
   - **預期**、**實際**
   - **環境**：本機 compose／k3s／EC2、假 AI 或真 AI、commit、日期
   - 除錯紀錄時：**症狀** → **排查過程** → **根本原因** → **修正方式**（檔案、commit）→ **預防／後續**
   - 效能測試：RPS、p50／p95／p99、錯誤率表格，和上次結果比較
5. 對話裡沒有的細節不要猜，標「未確認」。不使用任何表情符號。
6. 回報：寫在哪個資料庫、哪一列、哪些測試項目、連結。
