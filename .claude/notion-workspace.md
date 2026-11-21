# Notion 工作區對照表（AI Interview）

所有 `/notion-*` skill 共用這份檔案：寫入前先讀它，照這裡的位置和規則寫。

## 位置

根頁面「AI Interview」：https://app.notion.com/p/3f52dbee50808034b9e1dfc7442c118d

| 區塊 | 頁面 | 頁面下的 inline 資料庫 | 對應 skill |
|---|---|---|---|
| 產品規劃 | https://app.notion.com/p/4192dbee50808007bbcdeebb491a53b6 | 5 個 | `/notion-product` |
| 系統設計 | https://app.notion.com/p/3f52dbee508080388c8ef0a08cf657b6 | 6 個 | `/notion-design` |
| 系統開發 | https://app.notion.com/p/3f62dbee508080f3a37bc4aaee31aed2 | 3 個 | `/notion-dev` |
| 系統測試 | https://app.notion.com/p/4192dbee5080805e9df8e02743f4b837 | 4 個 | `/notion-test` |

## 資料庫快取

第一次執行某個 skill 時，fetch 該區塊頁面，把找到的 inline 資料庫填進下表（名稱、data source id、標題欄位、其他欄位、放什麼）。之後先用這張表，**寫入前仍要 fetch 目標資料庫確認欄位沒變**；有變就更新這張表。

<!-- 格式：| 區塊 | 資料庫名稱 | data source（collection://…） | 標題欄位 | 其他欄位 | 放什麼 | -->

| 區塊 | 資料庫名稱 | data source | 標題欄位 | 其他欄位 | 放什麼 |
|---|---|---|---|---|---|
| 產品規劃 | 產品規格 | `collection://4192dbee-5080-80dd-add8-000b8e41621b` | `Name` | — | 一個產品功能一列（已實作／規劃中／關閉），內文：為什麼做、怎麼用、規則、怎麼驗證 |
| 產品規劃 | 使用者回饋 | `collection://4192dbee-5080-8060-a77f-000bd76f7dda` | `Name` | — | 一則或一類真實使用者回饋一列；不可捏造 |
| 產品規劃 | 競品分析 | `collection://4192dbee-5080-8013-ba41-000b63f2a20d` | `Name` | — | 一個競品一列 |
| 產品規劃 | 行銷 | `collection://4192dbee-5080-805b-abd6-000b7d3a5074` | `Name` | — | 一個行銷主題／管道／活動一列 |
| 產品規劃 | 未來待做 | `collection://41f2dbee-5080-8160-9eb3-000bff3e015c` | `Name` | `狀態`（待評估／規劃中／進行中／已完成／不做）、`優先順序`（高／中／低）、`類型`（新功能／改善／技術債）、`相關功能`（多選：出題／面試建議／模擬面試／報告／求職準備）、`提出日期` | 之後可能要做的功能與改善，一個項目一列；內文：痛點、想法、跟現有功能的關係、Claude 建議（風險、優先順序理由）、下一步 |
| 系統設計 | API | `collection://4192dbee-5080-80e7-91a5-000bf619e7e0` | `Name` | — | 一個 API 種類一列（`00 通用約定`、`Auth：…`、`Interviews：…`）；每個種類頁面裡有 inline 資料庫「API 列表」，一支 API 一列（見 `/notion-design`） |
| 系統設計 | 背景任務＆Redis | `collection://3f62dbee-5080-80c9-b653-000bbb245176` | `Name` | — | 背景工作、佇列、outbox、Redis 用法（一個主題一列） |
| 系統設計 | PostgreSQL設計 | `collection://3f52dbee-5080-8046-a860-000b4c7dfc56` | `Name` | — | 一張資料表一列（標題就是表名，例 `users`），另有一列 `00 總覽`（全部表的 ER 圖、外鍵、索引） |
| 系統設計 | MinIO | `collection://3f62dbee-5080-8053-a7d0-000b3bf89d6a` | `Name` | — | 物件儲存與上傳（目前 1 列） |
| 系統設計 | 部署與架構 | `collection://4152dbee-5080-800e-bee5-000bb1a226c9` | `Name` | — | 架構總覽、Compose、k3s、不停機部署、上雲規劃（一個主題一列） |
| 系統設計 | 問題 | `collection://3f52dbee-5080-80ba-bc7a-000b0e573b70` | `Name` | — | 設計問答與決策紀錄，標題是問句（例：「部署資料庫要拉出去嗎？」） |
| 系統測試 | API | `collection://4192dbee-5080-8083-822a-000b66e10284` | `Name` | — | 一個 API 種類一列；每列頁面裡有 inline 資料庫「測試項目」 |
| 系統測試 | 資安測試 | `collection://4192dbee-5080-8083-99b0-000b7af0109c` | `Name` | — | 一種資安檢測類別一列；每列頁面裡有「測試項目」 |
| 系統測試 | 附載測試 | `collection://4192dbee-5080-8097-befc-000b48068034` | `Name` | — | 一種效能測試情境一列（smoke／load／stress…）；每列頁面裡有「測試項目」 |
| 系統測試 | 整合測試？（UI) | `collection://4192dbee-5080-808b-9a54-000b74379ec7` | `Name` | — | 一條端到端流程一列（e2e、部署演練、瀏覽器流程）；每列頁面裡有「測試項目」 |

## 共用規則

1. **先讀後寫**：fetch 區塊頁面 → 決定資料庫 → fetch 資料庫看欄位與既有列 → 查有沒有同主題的列。
   - 有 → 更新該列的屬性和內文對應段落，不要整頁重寫、不要重複新增。
   - 沒有 → 新增一列。
2. **照既有欄位填**：不自己加欄位、不改欄位名稱或選項；select 沒有合適選項時才新增選項，並在回報裡說明。
3. **只寫在上表的四個區塊裡**：不新建頂層頁面、不新建資料庫（使用者明確要求，或 skill 另有說明時除外）。
4. **判斷不了放哪個資料庫時，先問使用者**，列出候選資料庫和理由。
5. **內容以 repo 為準**：規格、API、資料表、指令都從程式碼與 `spec/`、`README.md`、`deploy/` 讀，不要自己編。對話與 repo 裡都找不到的細節標「未確認」。
6. **使用者的想法 vs. Claude 的建議分開寫**：Claude 自己加的建議放在標題為「Claude 建議」的段落。
7. **內文風格**：`##` 分段、比較用表格、流程用 ```txt 區塊、ER 圖用 ```mermaid、長程式碼放 toggle；繁體中文。
8. **不使用任何表情符號（emoji）**：頁面 icon、標題、屬性值、內文、表格、callout 都不用。
   - 建立頁面時不給 `icon`；callout 一律改用 `<quote>` 或粗體引導詞（callout 會帶圖示）。
   - 狀態用文字：「通過」「失敗」「未修正」「已實作」「規劃中」，不用勾勾、叉叉、燈號。
   - 更新既有頁面時，順手把該頁的表情符號（含頁面 icon）移除。
9. **表格儲存格裡不要放 `|`**（會把欄位切斷）；寫完 fetch 一頁抽查表格、toggle、mermaid 是否正常。
10. **完成後回報**：每個寫入位置（區塊 / 資料庫 / 列標題）、新增或更新、Notion 連結。

## Notion 方案限制

這個工作區**不是 Business 方案**，`notion-query-data-sources`（SQL／rows 查詢）會回 `entitlement_required`，不要用。
查既有列改用：`notion-search`（關鍵字＋看結果的 `parent_data_source_url`），或 fetch 資料庫的 view URL；新增前用列標題搜尋一次避免重複。

## 連不上 Notion 時

`.mcp.json` 已設定 Notion MCP（`https://mcp.notion.com/mcp`）。工具不存在或回 401 時，請使用者執行 `/mcp` → 選 notion → 登入授權，授權完再重跑 skill。不要改寫成本機檔案就當作完成。
