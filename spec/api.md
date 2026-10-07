# AI Interview — API 規格書

> 版本：v0.1・後端：FastAPI・Base URL：`/api/v1`
> 相關文件：[architecture.md](architecture.md)・[postgresql.md](postgresql.md)・[flows.md](flows.md)
> FastAPI 會自動產生 OpenAPI（`/api/v1/docs`），本文件是**設計依據**；實作後以程式產生的 OpenAPI 為準，兩者不一致時更新本文件。

---

## 1. 通用約定

### 1.1 格式

- JSON，欄位一律 `snake_case`；時間為 ISO 8601 UTC（`2026-10-06T06:32:00Z`）。
- ID 為 UUID 字串。
- 上傳檔案用 `multipart/form-data`。
- 列舉值用英文代碼，顯示文字由前端對照：

| 欄位 | 值 | 顯示 |
|---|---|---|
| `length_mode` | `quick` / `standard` / `deep` | 快速 3 題 / 標準 5 題 / 深入 8 題 |
| `persona` | `warm` / `real` / `tough` | 溫和引導 / 真實模擬 / 壓力面試 |
| `language` | `zh` / `en` / `mixed` | 中文 / English / 中英混合 |
| `difficulty` | `basic` / `medium` / `advanced` | 基礎 / 中等 / 進階 |
| `voice_mode` | `live` / `scripted` | 擬真語音 / 標準語音 |

### 1.2 認證

- 登入成功後：
  - `access_token`（JWT，15 分鐘）放在回應 body，前端存在記憶體，請求時帶 `Authorization: Bearer <token>`。
  - `refresh_token` 放在 `HttpOnly; Secure; SameSite=Strict; Path=/api/v1/auth` Cookie，有效 30 天，每次使用都輪替。
- Access token 過期回 `401 TOKEN_EXPIRED`，前端 `api.js` 自動呼叫 `/auth/refresh` 後重送一次。
- 除 `/auth/*` 外，所有端點都需要登入。所有資源都以目前使用者為範圍；存取別人的資源回 `404`（不洩漏存在與否）。

### 1.3 錯誤格式

```json
{
  "error": {
    "code": "STATE_CONFLICT",
    "message": "面試狀態已變更，請重新取得目前狀態",
    "details": { "current_version": 12 },
    "request_id": "req_01J9…"
  }
}
```

| HTTP | code | 說明 |
|---|---|---|
| 400 | `VALIDATION_ERROR` | 欄位驗證失敗，`details.fields` 列出欄位 |
| 401 | `UNAUTHENTICATED` / `TOKEN_EXPIRED` | 未登入 / token 過期 |
| 403 | `FORBIDDEN` / `PLAN_LIMIT_REACHED` | 無權限 / 超過方案用量 |
| 404 | `NOT_FOUND` | 資源不存在或不屬於你 |
| 409 | `STATE_CONFLICT` | 面試狀態版本不符或階段不允許此操作 |
| 409 | `ACTIVE_SESSION_EXISTS` | 已有進行中的面試 |
| 409 | `RESOURCE_NOT_READY` | 履歷／職缺尚在解析、TTS 尚未完成 |
| 413 | `FILE_TOO_LARGE` | 檔案過大 |
| 415 | `UNSUPPORTED_MEDIA_TYPE` | 檔案類型不支援 |
| 422 | `QUESTION_SET_TOO_SMALL` | 題組題數少於面試長度所需 |
| 429 | `RATE_LIMITED` | 請求過於頻繁，`Retry-After` 標頭 |
| 502 | `UPSTREAM_ERROR` | AI 服務錯誤 |
| 503 | `LIVE_UNAVAILABLE` | GPT‑Live 無法連線（前端應改用 `scripted`） |

### 1.4 分頁

列表採 cursor 分頁：`?limit=20&cursor=<opaque>`，回應：

```json
{ "items": [ … ], "next_cursor": "eyJ…" }
```

`next_cursor` 為 `null` 表示沒有下一頁。

### 1.5 非同步資源

需要 AI 處理的建立動作（履歷解析、題目生成、面試建議、報告）回 `202 Accepted` 與資源狀態，前端以 `GET` 輪詢（建議每 2 秒，最多 60 秒）或訂閱 SSE。狀態一律為：

`pending` → `running` / `processing` → `ready` / `succeeded` | `failed`

### 1.6 冪等與並行控制

- 建立型 `POST` 可帶 `Idempotency-Key: <uuid>`，24 小時內同一 key 回傳第一次的結果。**面試回答上傳必帶**。
- 面試狀態推進端點必帶 `expected_version`（來自上一次回應的 `state_version`），不符回 `409 STATE_CONFLICT`。

---

## 2. 端點總覽

| 模組 | 方法 | 路徑 | 說明 | UI |
|---|---|---|---|---|
| Auth | POST | `/auth/register` | 註冊 | 登入 |
| | POST | `/auth/login` | 登入 | 登入 |
| | POST | `/auth/refresh` | 換新 access token | — |
| | POST | `/auth/logout` | 登出 | 側欄 |
| | GET | `/auth/google/start` | Google OAuth 起點 | 登入 |
| | GET | `/auth/google/callback` | Google OAuth 回呼 | — |
| Me | GET | `/me` | 帳號＋個人檔案＋技能＋經歷＋完整度 | 個人檔案、側欄 |
| | PATCH | `/me/profile` | 更新基本資料、求職意向 | 個人檔案 |
| | POST | `/me/avatar` | 上傳大頭貼 | 個人檔案 |
| | PUT | `/me/skills` | 整批覆寫技能 | 個人檔案・技能 |
| | POST | `/me/experiences` | 新增經歷 | 個人檔案・經歷 |
| | PATCH | `/me/experiences/{id}` | 修改經歷 | |
| | DELETE | `/me/experiences/{id}` | 刪除經歷 | |
| | DELETE | `/me` | 刪除帳號 | 設定 |
| Resumes | POST | `/resumes` | 上傳履歷（非同步解析） | 個人檔案・履歷 |
| | GET | `/resumes` | 履歷列表 | 面試建議・履歷選單 |
| | GET | `/resumes/{id}` | 履歷詳情與解析結果 | |
| | PATCH | `/resumes/{id}` | 設為主要履歷、改名 | |
| | DELETE | `/resumes/{id}` | 刪除履歷 | |
| | GET | `/resumes/{id}/download` | 取得下載連結 | |
| Jobs | GET | `/jobs` | 搜尋職缺（含契合度） | 職缺找尋 |
| | GET | `/jobs/{id}` | 職缺詳情 | 職缺詳情 |
| | POST | `/jobs` | 建立自訂職缺（貼上 JD） | 新增職缺 |
| | PATCH | `/jobs/{id}` | 修改自訂職缺 | |
| | DELETE | `/jobs/{id}` | 刪除自訂職缺 | |
| | PUT | `/jobs/{id}/save` | 收藏 | 收藏 |
| | DELETE | `/jobs/{id}/save` | 取消收藏 | |
| | GET | `/me/saved-jobs` | 我的收藏 | |
| | GET | `/me/jobs` | 我練習過或自訂的職缺（下拉選單用） | 各頁職缺選單 |
| Preps | POST | `/preps` | 生成面試建議 | 面試建議・重新生成 |
| | GET | `/preps/latest` | 取得某職缺×履歷最新的建議 | 面試建議 |
| | GET | `/preps/{id}` | 取得建議 | |
| | PATCH | `/preps/{id}/checklist/{item_id}` | 勾選準備清單 | 準備清單 |
| | POST | `/preps/{id}/likely-questions/add-to-set` | 可能題目加入題庫 | 全部加入題庫 |
| Question Bank | GET | `/question-sets` | 題組列表 | 題目生成 |
| | POST | `/question-sets` | 建立題組 | |
| | GET | `/question-sets/{id}` | 題組與題目 | 目前題組 |
| | PATCH | `/question-sets/{id}` | 改名 | |
| | DELETE | `/question-sets/{id}` | 刪除題組 | |
| | POST | `/question-sets/{id}/generations` | AI 生成題目 | 生成題目 |
| | GET | `/question-generations/{id}` | 生成進度 | |
| | POST | `/question-sets/{id}/items` | 新增題目 | 新增題目 |
| | PATCH | `/question-sets/{id}/items/{item_id}` | 修改題目 | 編輯 |
| | DELETE | `/question-sets/{id}/items/{item_id}` | 刪除題目 | 刪除 |
| | PUT | `/question-sets/{id}/order` | 重新排序 | 上移／下移 |
| Interviews | POST | `/interviews` | 建立面試場次 | 開始面試 |
| | GET | `/interviews` | 面試歷史 | 首頁・最近的面試 |
| | GET | `/interviews/{id}` | 場次狀態（重連用） | 面試進行中 |
| | DELETE | `/interviews/{id}` | 刪除面試（含音訊） | |
| | POST | `/interviews/{id}/live/connect` | WebRTC SDP 交換 | 面試進行中 |
| | POST | `/interviews/{id}/start` | 開始，取得第一題 | |
| | POST | `/interviews/{id}/questions/{qid}/playback-finished` | 主題目播放完成 | |
| | POST | `/interviews/{id}/questions/{qid}/answers` | 開始作答 | 點麥克風 |
| | POST | `/interviews/{id}/attempts/{aid}/complete` | 上傳回答並取得下一步 | 再點麥克風 |
| | POST | `/interviews/{id}/questions/{qid}/skip` | 跳過這題 | 跳過這題 |
| | POST | `/interviews/{id}/heartbeat` | 心跳 | — |
| | POST | `/interviews/{id}/end` | 提前結束 | 結束並看評分 |
| Reports | GET | `/interviews/{id}/report` | 評分報告 | 面試評分 |
| | GET | `/interviews/{id}/report/events` | 報告進度（SSE） | 報告產生中 |
| | GET | `/interviews/{id}/answers/{aid}/audio` | 取得回答音訊連結 | 逐題回顧 |
| | POST | `/interviews/{id}/report/add-to-set` | 題目加入題庫複習 | 加入題庫複習 |
| | POST | `/interviews/{id}/questions/{qid}/re-evaluate` | 重新評分 | 需複查題目 |
| Dashboard | GET | `/dashboard` | 首頁彙整 | 首頁 |
| Admin | POST | `/admin/jobs/import` | 匯入平台職缺 | 後台 |

---

## 3. Auth

### `POST /auth/register`

```json
// Request
{ "email": "pinyu.chen@example.com", "password": "********", "display_name": "陳品妤" }
// 201
{ "access_token": "eyJ…", "user": { "id": "…", "email": "…", "display_name": "陳品妤", "plan": "free" } }
```

密碼至少 8 碼。Email 已存在回 `409 EMAIL_TAKEN`。同時 `Set-Cookie: refresh_token=…`。

### `POST /auth/login`

Request `{ "email", "password" }`，回應同註冊。連續失敗 5 次鎖 15 分鐘（`429`）。

### `POST /auth/refresh`

讀 Cookie，回 `{ "access_token": "…" }` 並輪替 Cookie。若偵測到已撤銷的 token 被重用，撤銷整個 family 並回 `401`。

### `POST /auth/logout`

撤銷目前 refresh token，清除 Cookie，回 `204`。

### `GET /auth/google/start` → `302` 到 Google；`GET /auth/google/callback` → 建立／連結帳號後 `302 /#home`，並設 Cookie。

---

## 4. Me（個人檔案）

### `GET /me`

```json
{
  "user": { "id": "…", "email": "pinyu.chen@example.com", "display_name": "陳品妤", "avatar_url": "https://…", "plan": "free" },
  "profile": {
    "full_name": "陳品妤", "current_title": "前端工程師", "years_experience": 3,
    "phone": "0912-345-678", "bio": "喜歡把複雜流程變簡單的前端工程師…",
    "desired_roles": ["資深前端工程師", "UI 工程師"],
    "desired_locations": "台北市、新北市、可遠端",
    "expected_salary": null, "portfolio_url": null
  },
  "skills": ["React", "TypeScript", "Next.js", "CSS / Tailwind", "Jest", "Figma", "Git"],
  "experiences": [
    { "id": "…", "kind": "work", "organization": "Shoply Taiwan", "title": "前端工程師",
      "start_date": "2023-12-01", "end_date": null, "is_current": true,
      "description": "負責會員中心與結帳流程…" }
  ],
  "primary_resume": { "id": "…", "filename": "陳品妤_履歷_2026.pdf", "parse_status": "parsed" },
  "completeness": { "percent": 85, "missing": ["portfolio_url", "expected_salary"] }
}
```

完整度計算（後端程式）：姓名、職稱、Email、手機、自我介紹、主要履歷、≥3 個技能、≥1 段經歷、想找的職位、希望地點、期望月薪、作品集，共 12 項；可以調整權重。

### `PATCH /me/profile`

部分更新 `profile` 欄位與 `display_name`。回傳同 `GET /me`。

### `POST /me/avatar`

`multipart/form-data`，欄位 `file`（jpg/png/webp，≤ 2 MB）。回 `{ "avatar_url": "…" }`。

### `PUT /me/skills`

```json
{ "skills": ["React", "TypeScript", "Next.js"] }
```

最多 30 個，大小寫不分去重，依陣列順序存。回 `{ "skills": [...] }`。

### `POST /me/experiences`、`PATCH /me/experiences/{id}`、`DELETE /me/experiences/{id}`

Body 與 `experiences[]` 元素相同（不含 `id`）。

### `DELETE /me`

Body `{ "password": "…" }`（OAuth 帳號改為 `{ "confirm": "DELETE" }`）。回 `202`，背景刪除所有資料與檔案。

---

## 5. Resumes（履歷）

### `POST /resumes`

`multipart/form-data`：`file`（`.pdf` / `.doc` / `.docx`，≤ 10 MB）、`set_primary`（boolean，預設：沒有主要履歷時為 true）。

```json
// 202
{ "id": "…", "filename": "陳品妤_履歷_2026.pdf", "size_bytes": 421888,
  "parse_status": "pending", "is_primary": true, "created_at": "2026-09-28T03:10:00Z" }
```

同一使用者上傳相同檔案（sha256 相同）時，直接回傳既有履歷。

### `GET /resumes`

```json
{ "items": [ { "id": "…", "filename": "陳品妤_履歷_2026.pdf", "size_bytes": 421888,
               "is_primary": true, "parse_status": "parsed", "created_at": "…" } ] }
```

### `GET /resumes/{id}`

```json
{
  "id": "…", "filename": "…", "size_bytes": 421888, "is_primary": true,
  "parse_status": "parsed", "parse_error": null,
  "parsed": { "summary": "…", "skills": ["React"], "experiences": [ … ], "achievements": ["結帳轉換率提升 12%"] },
  "analysis": { "highlights": ["成果有量化數字", "技能和職缺需求相符"],
                "improvements": ["補上帶人或 mentor 經驗", "專案描述可以加上你的角色"] },
  "created_at": "…"
}
```

### `PATCH /resumes/{id}`

`{ "is_primary": true }` 或 `{ "filename": "…" }`。設為主要時會在同一交易取消其他履歷的主要標記，並排程重算契合度。

### `DELETE /resumes/{id}`

回 `204`。已被面試場次引用的履歷仍可刪除（場次保存的是快照），但原檔會從物件儲存刪除。

### `GET /resumes/{id}/download`

回 `{ "url": "https://…", "expires_in": 300 }`。

---

## 6. Jobs（職缺）

### `GET /jobs`

| Query | 說明 |
|---|---|
| `q` | 關鍵字（職稱、公司、技能） |
| `city` | `台北市` 等 |
| `filters` | 逗號分隔：`foreign`、`english`、`remote`、`hybrid`、`match80` |
| `sort` | `match`（預設）/ `recent` |
| `scope` | `all`（預設，平台＋自訂）/ `catalog` / `mine` |
| `limit`、`cursor` | 分頁 |

```json
{
  "total": 6,
  "items": [
    {
      "id": "…", "source": "catalog",
      "company_name": "Northwind Labs", "logo_text": "N", "logo_url": null,
      "title": "Senior Frontend Engineer",
      "city": "台北市", "location_text": "台北市信義區",
      "employment_type": "全職", "work_mode": "hybrid", "work_mode_note": "混合辦公",
      "salary_text": "月薪 75,000 – 95,000",
      "tags": ["React", "TypeScript", "設計系統"],
      "match_score": 92, "is_saved": false,
      "posted_at": "2026-10-06T00:00:00Z"
    }
  ],
  "next_cursor": null
}
```

`match_score` 依主要履歷計算；沒有主要履歷時為 `null`，前端顯示「上傳履歷看契合度」。

### `GET /jobs/{id}`

列表欄位，加上：

```json
{ "about": "Northwind 是總部在新加坡的餐飲 SaaS 公司…",
  "duties": ["主導後台前端架構與效能優化", "與設計師共建元件庫"],
  "requirements": ["3 年以上 React 經驗", "熟悉 TypeScript 與狀態管理"],
  "parse_status": "parsed" }
```

### `POST /jobs`（自訂職缺）

兩種建立方式：

```json
// A. 貼上 JD 原文 → 202，AI 非同步解析
{ "raw_text": "職稱：Senior Frontend Engineer\n公司：…" }

// B. 手動填寫 → 201
{ "company_name": "Northwind Labs", "title": "Senior Frontend Engineer",
  "location_text": "台北市信義區", "about": "…", "duties": ["…"], "requirements": ["…"], "tags": ["React"] }
```

回傳職缺物件，A 方式 `parse_status = "pending"`。

### `PATCH /jobs/{id}`、`DELETE /jobs/{id}`

只能修改／刪除 `source = user` 且屬於自己的職缺，否則 `404`。

### `PUT /jobs/{id}/save`、`DELETE /jobs/{id}/save`

回 `204`。

### `GET /me/saved-jobs`、`GET /me/jobs`

格式同 `GET /jobs`。`/me/jobs` 回傳「收藏、自訂、或曾經練習／生成過題目的職缺」，供各頁的職缺下拉選單使用。

---

## 7. Preps（面試建議）

### `POST /preps`

```json
// Request
{ "job_post_id": "…", "resume_id": "…" }
// 202
{ "id": "…", "status": "pending" }
```

履歷或職缺尚未解析完成回 `409 RESOURCE_NOT_READY`。

### `GET /preps/latest?job_post_id=…&resume_id=…`

沒有紀錄回 `404`，前端顯示「生成面試建議」按鈕。

### `GET /preps/{id}`

```json
{
  "id": "…", "status": "ready", "job_post_id": "…", "resume_id": "…",
  "match_score": 92,
  "verdict_title": "整體很適合你",
  "verdict_text": "技術條件幾乎都符合，要多準備「帶人」和「產品理解」的故事。",
  "strengths": ["3 年 React 經驗，符合年資要求", "有可量化的效能優化成果"],
  "gaps": ["履歷沒有寫到帶人或 mentor 經驗", "設計系統只有使用經驗，沒有建置經驗"],
  "checklist": [
    { "id": "…", "text": "實際操作 Northwind 的店家後台 demo", "is_checked": true },
    { "id": "…", "text": "準備 1 個帶新人或 review 的故事", "is_checked": false }
  ],
  "directions": [ { "label": "React 效能優化與實務經驗", "probability": 92 } ],
  "likely_questions": [
    { "index": 0, "priority": "high",
      "question": "請分享你主導過最有成就感的前端專案。",
      "why": "JD 強調「主導架構」，面試官想確認你有 owner 經驗。",
      "tip": "選結帳改版那段，用 STAR 說明你的角色，強調「轉換率 +12%」。" }
  ],
  "created_at": "…"
}
```

### `PATCH /preps/{id}/checklist/{item_id}`

`{ "is_checked": true }` → `204`。

### `POST /preps/{id}/likely-questions/add-to-set`

```json
// Request：不給 question_set_id 時，加到該職缺最近使用的題組（沒有就自動建立）
{ "question_set_id": null, "indexes": [0, 1, 2, 3] }
// 200
{ "question_set_id": "…", "added": 4, "skipped_duplicates": 0 }
```

加入的題目 `source = prep`，由 worker 補上 `rubric`。

---

## 8. Question Bank（題目生成與編輯）

### `GET /question-sets?job_post_id=…`

```json
{ "items": [ { "id": "…", "name": "Northwind 前端題組", "job_post_id": "…", "item_count": 5, "updated_at": "…" } ] }
```

### `POST /question-sets`

`{ "job_post_id": "…", "resume_id": "…", "name": "Northwind 前端題組" }` → `201`。

### `GET /question-sets/{id}`

```json
{
  "id": "…", "name": "…", "job_post_id": "…", "resume_id": "…",
  "items": [
    { "id": "…", "order_no": 1, "category": "自我介紹", "difficulty": "basic",
      "text": "請用兩分鐘介紹你自己，以及為什麼想加入 Northwind Labs？",
      "competency": "表達與動機", "source": "ai",
      "source_reason": "職缺為新創，重視候選人對產品的了解" }
  ],
  "pending_generation": null
}
```

`rubric` 與 `expected_points` 不回傳給前端（避免使用者背答案，也減少傳輸）。

### `POST /question-sets/{id}/generations`

```json
// Request
{ "categories": ["技術深度", "系統設計", "團隊合作"],
  "difficulty": "medium",
  "count": 5,
  "note": "多問一些系統設計和帶人經驗" }
// 202
{ "id": "…", "status": "pending" }
```

| 欄位 | 規則 |
|---|---|
| `categories` | 1–6 個，值：自我介紹、技術深度、系統設計、團隊合作、情境題、動機與規劃 |
| `difficulty` | `basic` / `medium` / `advanced` |
| `count` | 3–10 |
| `note` | 選填，≤ 300 字 |

生成的題目**附加到題組最後**，並與既有題目去重。同一題組同時只能有一個生成工作（`409`）。

### `GET /question-generations/{id}`

```json
{ "id": "…", "status": "succeeded", "created_item_count": 2, "skipped_duplicates": 3, "error": null }
```

### `POST /question-sets/{id}/items`

```json
{ "text": "你會怎麼幫助一位剛加入的初階工程師快速上手？", "category": "團隊合作", "difficulty": "medium" }
```

`category` 預設「自訂」、`difficulty` 預設 `medium`。新增到最後。`201` 回傳題目，worker 非同步補上 `rubric`。

### `PATCH /question-sets/{id}/items/{item_id}`

可改 `text`、`category`、`difficulty`；改了 `text` 會重新產生 `rubric`。`text` 不可空白（`400`）。

### `DELETE /question-sets/{id}/items/{item_id}`

`204`，剩下題目重新排號。

### `PUT /question-sets/{id}/order`

```json
{ "item_ids": ["id3", "id1", "id2", "id4", "id5"] }
```

必須包含題組中全部題目，否則 `400`。上移／下移由前端交換陣列後整批送出。

---

## 9. Interviews（面試，核心）

### 9.1 場次物件

所有面試端點都回傳或內含這個物件：

```json
{
  "id": "…",
  "status": "in_progress",
  "phase": "awaiting_answer",
  "state_version": 7,
  "voice_mode": "live",
  "length_mode": "standard",
  "persona": "warm",
  "language": "zh",
  "job": { "id": "…", "company_name": "Northwind Labs", "title": "Senior Frontend Engineer" },
  "progress": { "current_main_no": 2, "total_main": 5, "answered": 1, "skipped": 0 },
  "current_question": {
    "id": "…", "kind": "main", "order_no": 2, "followup_no": 0,
    "category": "技術深度",
    "text": "你在過去專案中如何處理 React 應用的效能問題？請舉一個實際例子。",
    "tts_url": "https://…/q2.mp3",
    "tts_expires_in": 300
  },
  "current_attempt": null,
  "limits": { "answer_max_sec": 300, "silence_prompt_sec": 12, "heartbeat_sec": 15 },
  "started_at": "…",
  "elapsed_sec": 214
}
```

`phase` 決定前端能做什麼：

| phase | 前端畫面 | 允許的呼叫 |
|---|---|---|
| `asking` | Ava 說話中，麥克風停用 | `playback-finished`、`skip`、`end` |
| `awaiting_answer` | 「輪到你了，點麥克風開始回答」 | `answers`（開始作答）、`skip`、`end` |
| `answering` | 錄音中、波形動畫 | `complete`、`end` |
| `finalizing` | 上傳中 | （等待 `complete` 回應） |
| `done` | 結束，導向報告 | `GET report` |

### 9.2 `POST /interviews`（建立場次）

```json
// Request
{
  "job_post_id": "…",
  "resume_id": "…",
  "question_set_id": "…",
  "length_mode": "standard",
  "persona": "warm",
  "language": "zh",
  "voice_mode": "live",
  "recording_consent": true,
  "auto_fill": true
}
```

處理規則：

1. `recording_consent` 必須為 `true`。
2. 依 `length_mode` 決定主題目數：`quick` 3、`standard` 5、`deep` 8。從題組**依順序取前 N 題**。
3. 題組題數不足：`auto_fill = true` 時 AI 依履歷＋職缺補足（補的題目也寫回題組）；否則回 `422 QUESTION_SET_TOO_SMALL`。
4. 沒給 `question_set_id`：用該職缺最近更新的題組；沒有題組時一律由 AI 生成。
5. 在同一交易：建立場次、寫入職缺與履歷快照、複製題目到 `session_questions`、排 TTS 工作（優先序 10）。
6. 已有 `ready` / `in_progress` 的場次回 `409 ACTIVE_SESSION_EXISTS`，`details.session_id` 讓前端可選擇繼續或結束舊場次。
7. 用量超過方案限制回 `403 PLAN_LIMIT_REACHED`。

```json
// 201
{ "session": { …場次物件，status: "preparing", phase: "idle"… },
  "questions": [ { "id": "…", "order_no": 1, "category": "自我介紹" } ] }
```

前端輪詢 `GET /interviews/{id}` 直到 `status = ready`（通常 < 5 秒）。題組未修改時 TTS 會重用快取。

### 9.3 `GET /interviews`

`?limit=20&cursor=…&status=completed`

```json
{ "items": [ { "id": "…", "ended_at": "2026-10-06T06:32:00Z",
               "job_label": "Northwind Labs・Senior Frontend Engineer",
               "status": "completed", "overall_score": 82, "report_status": "ready" } ],
  "next_cursor": null }
```

### 9.4 `GET /interviews/{id}`

回傳場次物件。用於：頁面重新整理、斷線重連、`409` 後重新同步。若 `phase = answering` 且距離 `current_attempt.answer_started_at` 已超過 `answer_max_sec + 30` 秒，後端會先將 attempt 標記 `interrupted`、`phase` 退回 `awaiting_answer` 再回傳。

### 9.5 `POST /interviews/{id}/live/connect`（只限 `voice_mode = live`）

前端建立 `RTCPeerConnection`、加入麥克風 track 與 data channel、產生 SDP offer，交給後端代為向 GPT‑Live 建立連線。

```json
// Request
{ "sdp_offer": "v=0\r\no=- 46117…" }
// 200
{ "sdp_answer": "v=0\r\no=- …", "live_session_ref": "…", "expires_at": "…" }
```

後端同時：以 `live_session_ref` 開啟 sideband WebSocket、送出系統指示（面試官角色、語言、風格、禁止自行出題）、記錄 `live_events`。

| 錯誤 | 前端處理 |
|---|---|
| `503 LIVE_UNAVAILABLE` | 提示「改用標準語音模式」，後端已把場次 `voice_mode` 改為 `scripted` |

> GPT‑Live 的 SDP 交換與 sideband 連接方式以官方文件為準，見 [architecture.md §11](architecture.md)。

### 9.6 `POST /interviews/{id}/start`

```json
// Request
{ "expected_version": 1 }
// 200
{ "session": { …phase: "asking"… },
  "intro": { "text": "你好，品妤！歡迎來到今天的面試。我們先從第一題開始：", "tts_url": "…" } }
```

前端依序播放 `intro.tts_url` 與 `current_question.tts_url`。

### 9.7 `POST /interviews/{id}/questions/{qid}/playback-finished`

```json
{ "expected_version": 2 }
```

`phase: asking → awaiting_answer`，寫入 `session_questions.asked_at`（**播放紀錄的依據**）。回傳場次物件。

> 若前端播放失敗（例如自動播放被瀏覽器擋住），前端顯示題目文字與「播放」按鈕；使用者看過題目後點「開始回答」時，前端仍先送 `playback-finished`，並在 body 帶 `{ "fallback": "text_shown" }`，後端記在 `live_events`。

### 9.8 `POST /interviews/{id}/questions/{qid}/answers`（開始作答）

```json
// Request
{ "expected_version": 3 }
// 201
{ "attempt": { "id": "…", "attempt_no": 1, "answer_started_at": "…" },
  "session": { …phase: "answering", state_version: 4… } }
```

前端收到後才開麥克風 track 並啟動 `MediaRecorder`。後端透過 sideband 告知 GPT‑Live「考生開始回答，安靜聆聽」。

### 9.9 `POST /interviews/{id}/attempts/{aid}/complete`（上傳回答並取得下一步）

**Headers**：`Idempotency-Key: <uuid>`（必填，前端重試時沿用同一個）

**Body**（`multipart/form-data`）：

| 欄位 | 型別 | 說明 |
|---|---|---|
| `audio` | file | `audio/webm;codecs=opus`（Chrome／Firefox）或 `audio/mp4`（Safari），≤ 15 MB |
| `meta` | JSON 字串 | 見下方 |

```json
// meta
{ "expected_version": 4,
  "client_started_at": "2026-10-08T07:01:10.120Z",
  "client_ended_at": "2026-10-08T07:03:52.800Z",
  "client_duration_ms": 162680,
  "end_trigger": "user_click" }
```

`end_trigger`：`user_click` / `max_duration` / `page_hide`。

處理順序：

1. 驗證狀態（`phase = answering` 且 attempt 屬於目前題目）。
2. 音訊上傳物件儲存 → 交易寫入 attempt（`saved`）、題目 `answered`、排 `transcribe_attempt` 工作。
3. 追問 Agent 判斷（同步，3 秒逾時）。
4. 決定下一步並推進狀態。

```json
// 200
{
  "attempt": { "id": "…", "status": "saved", "audio_duration_ms": 162680 },
  "next": {
    "type": "followup",
    "question": {
      "id": "…", "kind": "followup", "order_no": 2, "followup_no": 1,
      "text": "你剛提到虛擬捲動，為什麼選它而不是分頁？",
      "delivery": "live_voice",
      "tts_url": null
    }
  },
  "session": { …phase: "asking", state_version: 5… }
}
```

`next.type`：

| type | 意義 | 前端動作 |
|---|---|---|
| `followup` | 追問 | `delivery = live_voice`：打開 AI 音訊閘門讓 GPT‑Live 念出；`delivery = tts`：播放 `tts_url`。播完送 `playback-finished` |
| `question` | 下一道主題目 | 播放 `tts_url`（播放前先讓 GPT‑Live 短回應結束，最多等 2 秒） |
| `completed` | 全部完成 | 播放結尾語、導向報告頁 |

**錯誤與重試**：網路失敗或 `5xx` 時，前端保留錄音 Blob，以**同一個** `Idempotency-Key` 指數退避重試（1s、2s、4s，最多 5 次）；仍失敗時顯示「重新上傳」按鈕，**不得前進到下一題**。

### 9.10 `POST /interviews/{id}/questions/{qid}/skip`

```json
{ "expected_version": 3 }
```

允許在 `asking` 或 `awaiting_answer`。題目標記 `skipped`，回應格式同 `complete` 的 `next` 與 `session`（GPT‑Live 會說「沒關係，我們換下一題。」）。

### 9.11 `POST /interviews/{id}/heartbeat`

`{ "phase": "answering", "rtc_state": "connected" }` → `204`。每 15 秒一次；後端更新 `last_heartbeat_at`。

### 9.12 `POST /interviews/{id}/end`

```json
// Request
{ "expected_version": 5, "reason": "user_ended" }
// 200
{ "session": { "status": "completed", "phase": "done", … },
  "report": { "status": "processing" } }
```

若結束時正在 `answering`，前端應**先完成 `complete`** 再呼叫 `end`；若無法完成，該 attempt 標記 `interrupted`，不納入評分。未作答的主題目維持 `pending`，報告中顯示「未作答」。後端關閉 sideband 與 GPT‑Live 連線。

### 9.13 `DELETE /interviews/{id}`

`204`。刪除場次、作答、評分、報告與所有音訊檔。

---

## 10. Reports（評分報告）

### `GET /interviews/{id}/report`

```json
{
  "status": "ready",
  "session": { "id": "…", "ended_at": "2026-10-06T06:32:00Z", "persona": "warm", "language": "zh",
               "job_label": "Northwind Labs・Senior Frontend Engineer" },
  "overall_score": 82,
  "delta_vs_prev": 5,
  "summary": "你的回答很有具體數據，這是最大的優勢。下次可以加強系統設計題的結構，並少用「嗯」「應該吧」這類讓人覺得不確定的詞。",
  "dimensions": [
    { "key": "structure",  "label": "內容結構", "score": 84 },
    { "key": "depth",      "label": "專業深度", "score": 80 },
    { "key": "fluency",    "label": "表達流暢", "score": 76 },
    { "key": "job_fit",    "label": "職缺契合", "score": 88 },
    { "key": "confidence", "label": "自信程度", "score": 72 }
  ],
  "metrics": { "filler_count": 7, "avg_answer_sec": 168, "speech_rate": 198, "speech_rate_unit": "chars_per_min" },
  "focus_areas": ["系統設計題用「目標 → 架構 → 流程 → 衡量」回答"],
  "progress": { "evaluated": 5, "total": 5 },
  "items": [
    {
      "question_id": "…", "order_no": 4, "category": "系統設計", "status": "answered",
      "question_text": "如果要從零建立一套給 5 個產品線共用的元件庫，你會怎麼規劃？",
      "answer": {
        "attempt_id": "…",
        "transcript": "嗯⋯我會先盤點各產品線現有的元件…",
        "transcript_status": "done",
        "duration_ms": 95000,
        "has_audio": true
      },
      "evaluation": {
        "status": "done",
        "score": 68,
        "highlight": "嗯⋯我會先盤點",
        "strengths": ["有提到 design token 與 Storybook，方向正確"],
        "improvements": ["開頭的「嗯⋯」和「應該會用吧」讓回答聽起來不確定"],
        "better_answer": "我會分四步：第一，訪談 5 個產品線…"
      },
      "followups": [
        { "question_id": "…", "followup_no": 1, "question_text": "…", "answer": { … }, "evaluation": { … } }
      ]
    }
  ]
}
```

- `status`：`pending` / `processing` / `ready` / `failed`。`processing` 時已完成的題目會先出現，`evaluation.status` 為 `pending` 的題目前端顯示骨架。
- `evaluation.status = needs_review` 時，`review_reason` 說明原因（例如「錄音幾乎沒有聲音」），前端提供「重新評分」。
- `status = skipped` 的題目沒有 `answer` 與 `evaluation`。
- 總分 = 已作答主題目分數平均（追問分數併入主題目，權重 30%），由程式計算。

### `GET /interviews/{id}/report/events`（SSE）

```
event: evaluation_done
data: {"question_id":"…","score":86}

event: report_ready
data: {"overall_score":82}

event: error
data: {"code":"UPSTREAM_ERROR"}
```

連線最長 120 秒；前端斷線後改為輪詢 `GET report`。

### `GET /interviews/{id}/answers/{aid}/audio`

`{ "url": "https://…", "expires_in": 300 }`。音訊超過保留期限回 `410 GONE`。

### `POST /interviews/{id}/report/add-to-set`

```json
// Request：不給 session_question_ids 時，預設加入全部分數 < 80 的題目
{ "question_set_id": null, "session_question_ids": ["…", "…"] }
// 200
{ "question_set_id": "…", "added": 2, "skipped_duplicates": 0 }
```

### `POST /interviews/{id}/questions/{qid}/re-evaluate`

只允許 `evaluation.status in (failed, needs_review)`，每題最多 2 次。`202`。

---

## 11. Dashboard（首頁）

### `GET /dashboard`

```json
{
  "greeting_name": "品妤",
  "last_session": {
    "id": "…", "overall_score": 82, "job_label": "Northwind Labs",
    "weakest_category": "系統設計"
  },
  "trend": [ { "date": "2026-09-20", "score": 62 }, { "date": "2026-10-06", "score": 82 } ],
  "trend_summary": { "latest": 82, "change_30d": 20 },
  "streak": { "days": 5, "week": [true, true, true, true, true, false, false] },
  "recent_sessions": [
    { "id": "…", "date": "2026-10-06", "job_label": "Northwind Labs・Senior Frontend Engineer", "score": 82 }
  ],
  "tip": { "title": "今日小提醒", "text": "回答系統設計題時，先講「目標」再講「做法」，面試官更容易跟上你的思路。" },
  "recommended_jobs": [
    { "id": "…", "title": "Senior Frontend Engineer", "company_name": "Northwind Labs", "logo_text": "N", "match_score": 92 }
  ]
}
```

- `trend`：最近 8 場已完成面試的總分。
- `streak.week`：本週一到週日是否有完成面試（使用者時區）。
- `tip`：依最近一次報告最弱的維度，從內建提示庫挑選。
- `recommended_jobs`：主要履歷契合度最高的 3 個平台職缺。

---

## 12. Admin

### `POST /admin/jobs/import`

需要 `admin` 角色。Body 為職缺陣列（欄位同 `POST /jobs` 方式 B，加 `external_ref`、`posted_at`、`city`、`work_mode`、`is_foreign`、`work_language`），以 `external_ref` upsert。回 `{ "inserted": 10, "updated": 2 }`，並排程計算 embedding。

---

## 13. 背景工作一覽（非 HTTP，供實作參考）

| kind | 觸發 | 結果 |
|---|---|---|
| `parse_resume` | `POST /resumes` | `resumes.parsed_json`、`analysis_json`、`embedding`；接著排 `compute_matches` |
| `parse_job` | `POST /jobs`（raw_text） | `job_posts` 欄位、`embedding` |
| `compute_matches` | 履歷解析完成、設為主要、職缺匯入 | `job_matches` |
| `generate_prep` | `POST /preps` | `interview_preps`、`prep_checklist_items` |
| `generate_questions` | `POST …/generations` | `question_set_items` |
| `build_rubric` | 使用者新增／修改題目、從建議加入題目 | `question_set_items.rubric` |
| `synthesize_tts` | `POST /interviews` | `session_questions.tts_audio_key`；全部完成後場次改為 `ready` |
| `transcribe_attempt` | `complete` | `answer_attempts.final_transcript`；接著排 `evaluate_attempt` |
| `evaluate_attempt` | 轉錄完成 | `evaluations`；若場次已結束且全部完成，排 `build_report` |
| `build_report` | 最後一題評分完成 | `interview_reports` |
| `expire_idle_sessions` | 每分鐘排程 | 無心跳場次改為 `aborted`，排剩下的評分與報告 |
| `purge_expired_audio` | 每日排程 | 刪除超過保留期限的音訊 |
| `maintain_partitions` | 每日排程 | 建立下個月分區、刪除過期分區 |
