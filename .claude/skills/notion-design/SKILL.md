---
name: notion-design
description: 把 API、PostgreSQL 資料表、架構與部署等設計內容同步到 Notion「系統設計」底下對應的 inline 資料庫；也可以檢查這些資料庫的分類是否重複並提出調整
argument-hint: [要記錄的設計主題；或「檢查分類」]
disable-model-invocation: true
---

把系統設計內容同步到 Notion「系統設計」。

使用者補充說明：$ARGUMENTS

## 先讀
- `.claude/notion-workspace.md`：位置、資料庫快取、共用規則（一定要照著做，特別是**不使用任何表情符號**）。
- 系統設計頁面：https://app.notion.com/p/3f52dbee508080388c8ef0a08cf657b6 ，底下有 6 個 inline 資料庫：API、背景任務＆Redis、PostgreSQL設計、MinIO、部署與架構、問題。

## 範圍判斷
- 有補充說明 → 照說明。
- 沒有 → 用 `git log --oneline -15` 和 `git diff main...HEAD --stat` 看這個分支改了什麼，找出受影響的設計：API（`backend/app/modules/*/router.py`）、資料表（`backend/migrations/*.sql`）、架構與流程（`spec/architecture.md`、`spec/flows.md`）、部署（`deploy/`、`spec/deploy/`）。
- 內容以**程式碼**為準，`spec/*.md` 可能落後（spec/api.md 停在 v0.4）。不一致的地方在回報裡列出。
- 一次改動常常要更新好幾個地方（例如新增 API＋新增欄位＋流程變了）。

## 一、API（資料庫「API」）

結構是兩層：

```txt
API（inline 資料庫）
 ├─ 00 通用約定                  ← 一般頁面：認證、錯誤格式、錯誤碼、分頁、非同步、冪等、限流、額度、端點總數
 ├─ Auth：註冊、登入、Google 登入  ← 一個「種類」一列（對應 backend/app/modules/<模組>）
 │    └─ API 列表（inline 資料庫）  ← 這個種類的每一支 API 一列
 │         ├─ POST /auth/register
 │         └─ ...
 └─ Interviews：面試狀態機 ...
```

- **種類頁面**：開頭一段說明（程式位置、用到的資料表、背景工作）→ 端點總表（Method、路徑、說明）→ inline 資料庫「API 列表」。
- **「API 列表」欄位**：`API`（標題，格式 `METHOD /路徑`，路徑不含 `/api/v1`）、`Method`（select）、`用途`（文字）、`認證`（select：不需登入／登入／後台工作階段／簽章網址）、`額度檢查`（checkbox，掛 `require_tokens` 的打勾）、`成功狀態碼`（文字）。新種類沒有這個資料庫時用 `notion-create-database` 建在種類頁面底下，欄位照上面。
- **每一支 API 頁面**一定要有這些段落：
  1. `## 用途`：這支 API 拿來做什麼、誰在什麼畫面呼叫
  2. `## Request`：`### Headers`（表格：Header、必填、說明；至少列 `Authorization`、`Content-Type`，有的話加 `Idempotency-Key`、`X-Admin-Session`、Cookie）、`### Path／Query 參數`（表格）、`### Body`（JSON 範例＋欄位表：欄位、型別、必填、限制、說明；沒有 body 寫「無」）
  3. `## Response`：`### 成功`（狀態碼、`### Headers`、`### Body` JSON 範例）、`### 錯誤`（表格：狀態碼、code、何時）
  4. `## 注意事項`：冪等、狀態機限制、非同步、副作用、前端該怎麼做
- 欄位與限制以 router 的 Pydantic 模型為準（`Field(max_length=…)`、`Literal[...]`）；回應以 service 回傳的 dict 為準。
- 新增或刪除 API 時，同步更新種類頁面的端點總表與 `00 通用約定` 的端點總數。

## 二、資料表（資料庫「PostgreSQL設計」）

- **一張表一列**，標題就是表名（例 `users`）。另有一列 `00 總覽`。
- **每張表的頁面**：
  1. `## 用途`：一兩句說明，加上所屬領域（帳號、會員、履歷、職缺、題庫、面試建議、面試、評分報告、求職準備、系統 Log）
  2. `## ER 圖`：```mermaid erDiagram，畫出這張表（所有欄位、型別、PK／FK／UK 標記）以及直接相關的表（參照誰、被誰參照，只寫表名與關聯）
  3. `## 欄位`：表格（欄位、型別、必填、預設、說明；說明取自 migration 註解）
  4. `## 約束`：主鍵、唯一、CHECK
  5. `## 外鍵`：表格（欄位、參照、ON DELETE），以及「被參照」（哪些表的哪個欄位指向這張表）
  6. `## 索引`：每個索引一行，說明用途（部分索引、唯一索引、GIN／HNSW 要說為什麼）
  7. `## 重點`：設計上要注意的地方（快照、軟刪除、狀態機、分區…）
  （欄位、約束、外鍵、索引已涵蓋完整結構，不另外貼 CREATE TABLE）
- **`00 總覽`**：全部表的 ER 圖（mermaid，只畫關聯，依領域加註解分組）、資料表清單（表名、領域、用途）、**全部外鍵**表格（表.欄位 → 參照表.欄位、ON DELETE）、**全部索引**表格（索引名、表、欄位、類型、條件）、擴充（citext、pg_trgm、vector）、分區表說明。
- **取得最終結構**：開暫時的 `pgvector/pgvector:pg16` 容器，依序套用 `backend/migrations/*.sql`，從 `pg_attribute`、`pg_constraint`、`pg_index`、`pg_get_indexdef` 讀結構，欄位註解取自 migration 檔，用完刪除容器。不要只看單一 migration 檔（後面的 ALTER 會改掉它）。
- 只更新這次動到的表；新表新增一列並更新 `00 總覽`。刪表時先問使用者要不要把那列移到垃圾桶。

## 三、架構／流程／部署
- 「部署與架構」：架構總覽、部署方式、不停機部署、上雲規劃，一個主題一列。
- 「背景任務＆Redis」：佇列、outbox、排程、Redis key 用法。
- 「MinIO」：物件儲存與上傳流程。
- 內文：```txt 流程圖、比較表格、為什麼這樣設計、取捨；規劃中的內容在第一段寫明「規劃中，尚未實作」。

## 四、設計問答（資料庫「問題」）
使用者在對話裡問「為什麼／要不要／怎麼選」並做出決定時，記成一列，標題用問句，內文：問題背景、選項比較、結論、影響到的檔案。

## 寫入規則
1. fetch 系統設計頁面與目標資料庫，對照 `.claude/notion-workspace.md` 的快取表。
2. 用 `notion-search`（帶 `data_source_url`）找既有列：有就更新對應段落，沒有才新增（這個工作區不能用 SQL 查詢）。
3. 不給頁面 icon、不用 callout、不用任何表情符號。
4. 寫完抽查：fetch 一頁確認表格、toggle、mermaid 正常。
5. 回報：每個資料庫新增／更新了哪些列與連結；程式與 spec 不一致的地方另外列出。

## 「檢查分類」模式
使用者輸入「檢查分類」或覺得資料庫重複時：
1. fetch 6 個資料庫的名稱、欄位、既有列標題。
2. 輸出一張表：每個資料庫放什麼、和哪個重疊、建議保留／合併／改名。
3. 提出建議的分類，說明每個既有列要搬到哪。
4. **只提案，等使用者同意後才搬移或改名**；刪除資料庫或頁面一定要使用者明確同意。
5. 同意後更新 `.claude/notion-workspace.md` 的快取表。
