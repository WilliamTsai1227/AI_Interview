# AI Interview — API 規格書

> 版本：v0.3・後端：FastAPI・Base URL：`/api/v1`
> 相關文件：[architecture.md](architecture.md)・[postgresql.md](postgresql.md)・[flows.md](flows.md)・[AI Interview.html](AI%20Interview.html)（UI 原型）
> FastAPI 會自動產生 OpenAPI（`/api/v1/docs`），本文件是**設計依據**；實作後以程式產生的 OpenAPI 為準，兩者不一致時更新本文件。

**v0.3 變更**：即時語音改為「串接式（`scripted`，MVP）＋ Realtime API（`realtime`，P3）」，不再使用 GPT‑Live；`live/connect` 改為 `realtime/connect`，`complete` 回應帶接話與追問的語音資訊，見 [architecture.md §6](architecture.md)。

**v0.2 主要變更**（對齊 ChatGPT 風格的 UI 原型與 UX 檢查）：

- 以「**目標職缺**」（`/targets`）為練習單位：加入時自動解析職缺、出第一組題目、產生面試建議；題組、面試建議都掛在目標職缺下（一個目標職缺一個題組）。
- 面試可**暫停**、**重錄**、離開後 **24 小時內繼續**；斷線不再直接作廢。
- `POST /interviews` 只需要 `target_id` 與三個選項，題數不足自動補題，不再回 `QUESTION_SET_TOO_SMALL`。
- 移除 Dashboard、收藏、技能／經歷／大頭貼等手動填寫的個人檔案端點；個人資料以履歷為準。

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
| `language` | `zh` / `en` / `mixed` | 中文 / 英文 / 中英混合 |
| `difficulty` | `basic` / `medium` / `advanced` | 基礎 / 中等 / 進階 |
| `voice_mode` | `scripted` / `realtime` | 由後端依方案與 Realtime 可用性決定，**使用者不需要選** |

### 1.2 認證

- 登入成功後：
  - `access_token`（JWT，15 分鐘）放在回應 body，前端存在記憶體，請求時帶 `Authorization: Bearer <token>`。
  - `refresh_token` 放在 `HttpOnly; Secure; SameSite=Strict; Path=/api/v1/auth` Cookie，有效 30 天，每次使用都輪替。
- Access token 過期回 `401 TOKEN_EXPIRED`，前端 `api.js` 自動呼叫 `/auth/refresh` 後重送一次，使用者不會看到登出。
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

`message` 是可以直接顯示給使用者的中文句子；前端不要顯示 `code` 或 `request_id`（`request_id` 放在「回報問題」裡）。

| HTTP | code | 說明 | 前端該怎麼做 |
|---|---|---|---|
| 400 | `VALIDATION_ERROR` | 欄位驗證失敗，`details.fields` 列出欄位 | 在欄位旁顯示訊息 |
| 401 | `UNAUTHENTICATED` / `TOKEN_EXPIRED` | 未登入 / token 過期 | 自動 refresh；失敗才導向登入 |
| 403 | `PLAN_LIMIT_REACHED` | 超過方案用量 | 顯示剩餘次數與升級說明 |
| 404 | `NOT_FOUND` | 資源不存在或不屬於你 | 回到上一層列表 |
| 409 | `STATE_CONFLICT` | 面試狀態版本不符或階段不允許此操作 | 重新 `GET /interviews/{id}` 同步畫面，不提示錯誤 |
| 409 | `ACTIVE_SESSION_EXISTS` | 已有未結束的面試，`details.session_id` | 顯示「繼續那場／結束它並開始新的」，不是錯誤訊息 |
| 409 | `REQUEST_IN_PROGRESS` | 同一個 Idempotency-Key 的請求仍在處理中 | 稍後自動重試 |
| 409 | `RESOURCE_NOT_READY` | 履歷／職缺尚在解析、題組尚在生成 | 顯示進度，完成後自動繼續 |
| 413 | `FILE_TOO_LARGE` | 檔案過大 | 顯示上限（履歷 10 MB） |
| 415 | `UNSUPPORTED_MEDIA_TYPE` | 檔案類型不支援 | 顯示支援格式 |
| 422 | `IDEMPOTENCY_KEY_REUSED` | 同一個 Idempotency-Key 搭配不同的請求內容 | 產生新 key 重送 |
| 422 | `JD_UNREADABLE` | 貼上的內容看不出是職缺（太短或沒有職稱） | 提示「至少要有職稱和工作內容」 |
| 429 | `RATE_LIMITED` | 請求過於頻繁，`Retry-After` 標頭 | 按鈕暫時停用並倒數 |
| 502 | `UPSTREAM_ERROR` | AI 服務錯誤 | 顯示「Ava 暫時忙不過來，請再試一次」與重試按鈕 |
| 503 | `REALTIME_UNAVAILABLE` | Realtime API 無法連線 | 自動改用標準語音（`scripted`），面試照常進行 |

### 1.4 分頁

列表採 cursor 分頁：`?limit=20&cursor=<opaque>`，回應：

```json
{ "items": [ … ], "next_cursor": "eyJ…" }
```

`next_cursor` 為 `null` 表示沒有下一頁。前端用無限捲動，不顯示頁碼。

### 1.5 非同步資源

需要 AI 處理的動作（履歷解析、職缺解析、題目生成、面試建議、報告）回 `202 Accepted` 與資源狀態，前端以 `GET` 輪詢（建議每 2 秒，最多 60 秒）或訂閱 SSE。狀態一律為：

`pending` → `running` / `processing` → `ready` / `succeeded` | `failed`

**UX 規則**：非同步期間畫面一定顯示「正在做什麼」（例如「Ava 正在依職缺出題⋯」）與骨架，不顯示空白；失敗時顯示可重試的按鈕，不讓使用者卡住。

### 1.6 冪等與並行控制

- 建立型 `POST` 可帶 `Idempotency-Key: <uuid>`，24 小時內同一 key 回傳第一次的結果。**面試回答上傳必帶**。
  - 實作：middleware 以 Redis `ai:idem:{user_id}:{key}` 記錄。第一次請求先寫入 `processing`；同 key 的請求還在處理中時回 `409 REQUEST_IN_PROGRESS`；完成後存入狀態碼與回應 body，之後同 key 直接回傳。
  - 同一 key 但 body 不同回 `422 IDEMPOTENCY_KEY_REUSED`。
  - Redis 不可用時，回答上傳退回使用 `answer_attempts.idempotency_key` 唯一約束判斷，仍保證不重複寫入。
- 面試狀態推進端點必帶 `expected_version`（來自上一次回應的 `state_version`），不符回 `409 STATE_CONFLICT`。

### 1.7 限流

計數放在 Redis（固定視窗，`ai:rl:{action}:{user_id}:{window}`）。超過回 `429 RATE_LIMITED`，附 `Retry-After` 標頭；回應一律帶 `X-RateLimit-Limit`、`X-RateLimit-Remaining`。

| action | 範圍 | 限制（free 方案預設，可由設定調整） |
|---|---|---|
| `login_fail` | 每個 Email | 15 分鐘內失敗 5 次即鎖定 15 分鐘 |
| `register` | 每個 IP | 每小時 10 次 |
| `resume_upload` | 每位使用者 | 每天 20 次 |
| `target_create` | 每位使用者 | 每小時 20 個 |
| `question_gen` | 每位使用者 | 每小時 20 次 |
| `prep_gen` | 每位使用者 | 每小時 20 次（重新分析＋再多產生合計） |
| `interview_create` | 每位使用者 | 每天 10 場（另受方案每月用量限制） |
| `default` | 每位使用者 | 每分鐘 120 次 |

Redis 不可用時 fail-open，改用程序內記憶體計數。

---

## 2. 端點總覽

| 模組 | 方法 | 路徑 | 說明 | UI |
|---|---|---|---|---|
| Auth | POST | `/auth/register` | 註冊 | 登入頁 |
| | POST | `/auth/login` | 登入 | 登入頁 |
| | POST | `/auth/refresh` | 換新 access token | — |
| | POST | `/auth/logout` | 登出 | 側欄・帳號選單 |
| | GET | `/auth/google/start` | Google 登入起點 | 登入頁 |
| | GET | `/auth/google/callback` | Google 登入回呼 | — |
| Me | GET | `/me` | 帳號、偏好、用量、上手狀態、未結束的面試 | 開啟網站時 |
| | PATCH | `/me` | 改名稱、偏好（預設長度／風格／語言、求職意向） | 設定・一般／求職意向／帳號 |
| | DELETE | `/me/interviews` | 刪除所有面試紀錄與錄音 | 設定・帳號 |
| | DELETE | `/me` | 刪除帳號 | 設定・帳號 |
| Resumes | POST | `/resumes` | 上傳履歷（非同步解析） | 設定・履歷、新面試 |
| | GET | `/resumes` | 履歷列表 | 新面試・履歷選單 |
| | GET | `/resumes/{id}` | 解析結果（經歷、技能、亮點） | 設定・履歷 |
| | PATCH | `/resumes/{id}` | 設為主要履歷、改名 | 設定・履歷 |
| | DELETE | `/resumes/{id}` | 刪除履歷 | 設定・履歷 |
| Targets | GET | `/targets` | 目標職缺列表（含題數、題組狀態、最近練習） | 目標職缺、側欄、題庫生成、面試建議、新面試・職缺選單 |
| | POST | `/targets` | 加入目標職缺（貼上 JD 或從職缺庫）＋自動出題＋面試建議 | 貼上新職缺、職缺詳情 |
| | GET | `/targets/{id}` | 目標職缺詳情＋準備進度 | 目標職缺詳情 |
| | PATCH | `/targets/{id}` | 換預設履歷、修正職缺內容 | 新面試・履歷選單、面試建議・依據卡片 |
| | DELETE | `/targets/{id}` | 移出目標職缺（資料保留） | 目標職缺詳情 |
| Questions | GET | `/targets/{id}/questions` | 題組 | 題庫生成、新面試・本次題目 |
| | POST | `/targets/{id}/questions/generate` | 依需求生成題目 | 題庫生成・輸入框 |
| | GET | `/question-generations/{id}` | 生成進度 | — |
| | POST | `/targets/{id}/questions` | 新增題目 | 新增題目 |
| | PATCH | `/targets/{id}/questions/{item_id}` | 修改題目 | 編輯 |
| | DELETE | `/targets/{id}/questions/{item_id}` | 刪除題目 | 刪除 |
| | PUT | `/targets/{id}/questions/order` | 重新排序 | 上移／下移 |
| Prep | GET | `/targets/{id}/prep` | 面試建議（加入目標職缺時自動產生）＋分析依據 | 面試建議・職缺詳情 |
| | POST | `/targets/{id}/prep/analyze` | 重新分析（重讀職缺與履歷；只新增不刪除） | 依據卡片・重新分析 |
| | POST | `/preps/{id}/likely-questions/generate` | 再多產生可能題目（附加） | 可能被問的題目・再多產生 |
| | POST | `/preps/{id}/checklist/generate` | 再多產生準備清單（附加） | 面試前準備清單・再多產生 |
| | GET | `/prep-generations/{id}` | 分析／產生進度 | — |
| | PATCH | `/preps/{id}/checklist/{item_id}` | 勾選準備清單 | 準備清單 |
| | POST | `/preps/{id}/likely-questions/add-to-set` | 可能題目加入題組 | 加入題組、全部加入題組 |
| Jobs | GET | `/jobs` | 職缺庫搜尋（含契合度） | 職缺搜尋 |
| | GET | `/jobs/{id}` | 職缺詳情 | 職缺詳情視窗 |
| Interviews | POST | `/interviews` | 建立面試 | 新面試・開始面試 |
| | GET | `/interviews/active` | 未結束（含暫停中）的面試 | 新面試橫幅、側欄 |
| | GET | `/interviews/{id}` | 場次狀態（重新整理、繼續用） | 面試進行中 |
| | POST | `/interviews/{id}/realtime/connect` | Realtime WebRTC SDP 交換（只限 `realtime`） | 面試進行中 |
| | POST | `/interviews/{id}/start` | 開始，取得第一題 | |
| | POST | `/interviews/{id}/questions/{qid}/playback-finished` | 主題目播放完成 | |
| | POST | `/interviews/{id}/questions/{qid}/answers` | 開始作答 | 點麥克風 |
| | POST | `/interviews/{id}/attempts/{aid}/complete` | 上傳回答並取得下一步 | 按 ■ |
| | POST | `/interviews/{id}/attempts/{aid}/discard` | 重錄（丟掉這次回答） | 重錄 |
| | POST | `/interviews/{id}/questions/{qid}/skip` | 跳過這題 | 跳過 |
| | POST | `/interviews/{id}/pause` | 暫停 | 暫停、離開頁面 |
| | POST | `/interviews/{id}/resume` | 繼續 | 繼續面試 |
| | POST | `/interviews/{id}/heartbeat` | 心跳 | — |
| | POST | `/interviews/{id}/end` | 結束（已作答的題目產生報告） | 結束面試 |
| Reports | GET | `/interviews` | 面試報告列表＋彙總 | 面試報告 |
| | GET | `/interviews/{id}/report` | 評分報告 | 報告詳情 |
| | GET | `/interviews/{id}/report/events` | 報告進度（SSE） | 評分中 |
| | GET | `/interviews/{id}/answers/{aid}/audio` | 回答錄音連結 | 聽我的錄音 |
| | POST | `/interviews/{id}/report/add-to-set` | 弱項加入題組 | 弱項加入題組 |
| | POST | `/interviews/{id}/questions/{qid}/re-evaluate` | 重新評分 | 需複查的題目 |
| | DELETE | `/interviews/{id}` | 刪除報告與錄音 | 報告・更多 |
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

密碼至少 8 碼。Email 已存在回 `409 EMAIL_TAKEN`（訊息：「這個 Email 已經註冊過，直接登入就好」）。同時 `Set-Cookie: refresh_token=…`。註冊完成直接進入「新面試」頁，不另外要求填個人資料。

### `POST /auth/login`

Request `{ "email", "password" }`，回應同註冊。連續失敗 5 次鎖 15 分鐘（`429`）。

### `POST /auth/refresh`

讀 Cookie，回 `{ "access_token": "…" }` 並輪替 Cookie。若偵測到已撤銷的 token 被重用，撤銷整個 family 並回 `401`。

### `POST /auth/logout`

撤銷目前 refresh token，清除 Cookie，回 `204`。

### `GET /auth/google/start` → `302` 到 Google；`GET /auth/google/callback` → 建立／連結帳號後 `302 /#new`，並設 Cookie。

---

## 4. Me（帳號與偏好）

### `GET /me`

前端開啟網站時呼叫一次，決定顯示哪個畫面（新使用者引導、未結束面試橫幅）。

```json
{
  "user": { "id": "…", "email": "pinyu.chen@example.com", "display_name": "陳品妤", "plan": "pro" },
  "preferences": {
    "default_length": "standard", "default_persona": "warm", "default_language": "zh",
    "desired_roles": ["資深前端工程師", "UI 工程師"], "desired_locations": ["台北市", "可遠端"]
  },
  "usage": { "period": "2026-10", "interviews_used": 12, "interviews_limit": 30 },
  "onboarding": { "has_resume": true, "has_target": true, "has_completed_interview": true },
  "primary_resume": { "id": "…", "filename": "陳品妤_履歷_2026.pdf", "parse_status": "parsed" },
  "active_interview": {
    "id": "…", "status": "paused", "target_id": "…", "company_name": "Acme Cloud",
    "answered": 1, "current_main_no": 2, "total_main": 5, "resume_deadline": "2026-10-10T08:00:00Z"
  }
}
```

- `onboarding` 讓前端顯示對應的引導：沒有目標職缺時，「新面試」頁的主按鈕是「貼上職缺內容」。
- `active_interview` 為 `null` 表示沒有未結束的面試；有的話，「新面試」頁顯示「繼續面試／結束並產生報告」橫幅，側欄顯示「面試暫停中」。

### `PATCH /me`

```json
{ "display_name": "陳品妤",
  "preferences": { "default_length": "quick", "desired_locations": ["台北市"] } }
```

`preferences` 為部分更新（merge）。**新面試頁每次開始面試時，後端也會把當次的長度／風格／語言寫回 `preferences`**，下次打開就是上次的設定。回傳同 `GET /me`。

### `DELETE /me/interviews`

刪除所有面試、報告、錄音。Body `{ "confirm": "DELETE" }`，回 `202`。

### `DELETE /me`

Body `{ "password": "…" }`（Google 帳號改為 `{ "confirm": "DELETE" }`）。回 `202`，背景刪除所有資料與檔案。

> 不提供技能、經歷、手機、大頭貼等編輯端點：經歷與技能以履歷解析結果為準，使用者不需要重複填寫。解析錯誤時，修改履歷檔重新上傳即可。

---

## 5. Resumes（履歷）

### `POST /resumes`

`multipart/form-data`：`file`（`.pdf` / `.doc` / `.docx`，≤ 10 MB）、`set_primary`（boolean，預設：沒有主要履歷時為 true）。

```json
// 202
{ "id": "…", "filename": "陳品妤_履歷_2026.pdf", "size_bytes": 421888,
  "parse_status": "pending", "is_primary": true, "created_at": "2026-09-28T03:10:00Z" }
```

- 同一使用者上傳相同檔案（sha256 相同）時，直接回傳既有履歷。
- 第一份履歷解析完成後，系統會為「還沒有預設履歷」的目標職缺補上 `resume_id`，並重新產生它們的面試建議（題組不會被覆蓋）。

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
  "parsed": {
    "summary": "…",
    "skills": ["React", "TypeScript", "Next.js"],
    "experiences": [ { "org": "Shoply Taiwan", "title": "前端工程師", "start": "2023-12", "end": null } ],
    "achievements": ["結帳轉換率提升 12%"]
  },
  "analysis": { "highlights": ["成果有量化數字", "技能和職缺需求相符"],
                "improvements": ["補上帶人或 mentor 經驗", "專案描述可以加上你的角色"] },
  "created_at": "…"
}
```

設定頁以唯讀方式顯示 `parsed`（「Ava 從履歷讀到的」），讓使用者確認 Ava 讀對了。

### `PATCH /resumes/{id}`

`{ "is_primary": true }` 或 `{ "filename": "…" }`。設為主要時會在同一交易取消其他履歷的主要標記，並排程重算契合度。

### `DELETE /resumes/{id}`

回 `204`。已被面試場次引用的履歷仍可刪除（場次保存的是快照），但原檔會從物件儲存刪除。

---

## 6. Targets（目標職缺）

目標職缺是使用者「正在準備的職缺」，也是練習的單位：題組、面試建議、面試、報告都掛在它下面。

### 6.1 目標職缺物件

```json
{
  "id": "…",
  "job": { "id": "…", "source": "user", "company_name": "Acme Cloud", "title": "Backend Engineer",
           "location_text": null, "tags": ["Python"], "parse_status": "parsed" },
  "resume": { "id": "…", "filename": "陳品妤_履歷_2026.pdf" },
  "match_score": 80,
  "question_set": { "status": "ready", "item_count": 5 },
  "prep": { "id": "…", "status": "ready", "likely_count": 4, "checklist_done": 2, "checklist_total": 5 },
  "last_report": { "session_id": "…", "overall_score": 82, "ended_at": "2026-10-06T06:32:00Z" },
  "last_practiced_at": "2026-10-06T06:32:00Z",
  "created_at": "…"
}
```

`question_set.status = preparing` 時，前端顯示「Ava 正在依職缺出題⋯」並停用「開始面試」按鈕。`prep` 與 `last_report` 用於目標職缺詳情的「準備進度」（題庫題數、建議與清單完成度、最近一場分數）。介面上 `question_set` 稱為「面試題庫」。

### `GET /targets`

依 `last_practiced_at`（沒練過的依建立時間）排序，回 `{ "items": [ …目標職缺物件… ] }`。不分頁（MVP 上限 30 個）。

### `POST /targets`（加入目標職缺）

**一次呼叫完成「加入職缺 → 解析 → 出第一組題目 → 產生面試建議」**，使用者不用到各頁分別操作。

```json
// A. 貼上職缺內容（最常見）
{ "raw_text": "Backend Engineer｜Acme Cloud\n工作內容：…", "resume_id": null }

// B. 從職缺庫加入
{ "job_post_id": "…", "resume_id": null }
```

- `resume_id` 省略時用主要履歷；使用者沒有履歷時為 `null`，題目與建議只依職缺產生。
- 方式 A：建立 `job_posts`（`source = user`），排 `parse_job`；解析完成後才排出題與面試建議。
- 方式 B：職缺已解析，直接排 `generate_questions`（初始題組，預設 8 題、涵蓋各題型）與 `generate_prep`。
- 已經是目標職缺（含已移出的）時回 `200` 與既有物件，並把 `status` 改回 `active`（找回原本的題組與報告）。
- 貼上的內容太短或看不出職稱：`422 JD_UNREADABLE`。

```json
// 202
{ …目標職缺物件，job.parse_status: "pending"，question_set.status: "preparing"，prep.status: "pending"… }
```

前端輪詢 `GET /targets/{id}` 直到 `question_set.status = ready`（通常 10 秒內）。

### `GET /targets/{id}`

目標職缺物件，`job` 內含完整職缺內容（`about`、`duties`、`requirements`、`salary_text`…）。

### `PATCH /targets/{id}`

```json
{ "resume_id": "…" }
// 或修正 AI 解析錯的職缺內容（僅 source = user 的職缺）
{ "job": { "title": "Senior Backend Engineer", "company_name": "Acme Cloud" } }
```

換預設履歷或修正職缺內容後，系統自動重新分析面試建議（只新增、不刪除）；題組不變（使用者編輯過的題目不被覆蓋）。

### `DELETE /targets/{id}`

移出目標職缺：`status` 改為 `archived`，回 `204`。題組、面試建議、報告都保留，再次 `POST /targets` 同一職缺即可找回。有未結束的面試時回 `409 ACTIVE_SESSION_EXISTS`。

---

## 7. Questions（題組）

每個目標職缺剛好一個題組。新面試依題組**順序**取前 N 題。

### `GET /targets/{id}/questions`

```json
{
  "status": "ready",
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

### `POST /targets/{id}/questions/generate`

```json
// Request：只有 note 是使用者打的字，其他都有預設值
{ "note": "多問一些系統設計和帶人經驗",
  "categories": ["技術深度", "系統設計", "團隊合作"],
  "difficulty": "medium",
  "count": 5 }
// 202
{ "id": "…", "status": "pending" }
```

| 欄位 | 規則 | 預設 |
|---|---|---|
| `note` | 選填，≤ 300 字 | 空 |
| `categories` | 0–6 個：自我介紹、技術深度、系統設計、團隊合作、情境題、動機與規劃 | 空＝由 Ava 依 `note` 與職缺決定 |
| `difficulty` | `basic` / `medium` / `advanced` | `medium` |
| `count` | 3–10 | 5 |

生成的題目**附加到題組最後**，並與既有題目去重。同一題組同時只能有一個生成工作（`409`）。

### `GET /question-generations/{id}`

```json
{ "id": "…", "status": "succeeded", "created_item_count": 2, "skipped_duplicates": 3, "error": null }
```

`skipped_duplicates > 0` 時前端提示「有 3 題和現有題目重複，已略過」。

### `POST /targets/{id}/questions`

```json
{ "text": "你會怎麼幫助一位剛加入的初階工程師快速上手？", "category": "團隊合作", "difficulty": "medium" }
```

`category` 預設「自訂」、`difficulty` 預設 `medium`。新增到最後。`201` 回傳題目，worker 非同步補上 `rubric`。

### `PATCH /targets/{id}/questions/{item_id}`

可改 `text`、`category`、`difficulty`；改了 `text` 會重新產生 `rubric`。`text` 不可空白（`400`）。

### `DELETE /targets/{id}/questions/{item_id}`

`204`，剩下題目重新排號。

### `PUT /targets/{id}/questions/order`

```json
{ "item_ids": ["id3", "id1", "id2", "id4", "id5"] }
```

必須包含題組中全部題目，否則 `400`。上移／下移由前端交換陣列後整批送出。

---

## 8. Prep（面試建議）

- 加入目標職缺時自動產生，使用者打開「面試建議」頁就有內容。
- 頁面先列出目標職缺（`GET /targets` 的 `prep` 摘要），點進去才是單一職缺的面試建議。
- **每次分析與產生都會重新讀取這個職缺的完整內容和目前選的履歷**，並把「依據」顯示在頁面上方，讓使用者知道建議是根據什麼產生的。
- **只新增、不刪除**：「重新分析」會更新契合度、優勢、需要補強與高機率方向；可能題目與準備清單只會**附加**新發現的項目，原本的項目、勾選狀態、已加入題組的標記都保留。

### `GET /targets/{id}/prep`

```json
{
  "id": "…", "status": "ready", "target_id": "…",
  "basis": {
    "job": { "id": "…", "company_name": "Northwind Labs", "title": "Senior Frontend Engineer", "updated_at": "2026-10-01T02:00:00Z" },
    "resume": { "id": "…", "filename": "陳品妤_履歷_2026.pdf", "parsed_at": "2026-09-28T03:12:00Z" },
    "analyzed_at": "2026-10-06T06:32:00Z",
    "stale": false
  },
  "match_score": 92,
  "verdict_title": "整體很適合你",
  "verdict_text": "技術條件幾乎都符合，要多準備「帶人」和「產品理解」的故事。",
  "strengths": ["3 年 React 經驗，符合年資要求", "有可量化的效能優化成果"],
  "gaps": ["履歷沒有寫到帶人或 mentor 經驗", "設計系統只有使用經驗，沒有建置經驗"],
  "directions": [ { "label": "React 效能優化與實務經驗", "probability": 92 } ],
  "likely_questions": [
    { "id": "…", "order_no": 1, "priority": "high",
      "question": "請分享你主導過最有成就感的前端專案。",
      "why": "JD 強調「主導架構」，面試官想確認你有 owner 經驗。",
      "tip": "選結帳改版那段，用 STAR 說明你的角色，強調「轉換率 +12%」。",
      "source": "initial", "created_at": "…", "in_set": false }
  ],
  "checklist": [
    { "id": "…", "order_no": 1, "text": "實際操作 Northwind 的店家後台 demo", "is_checked": true, "source": "initial", "created_at": "…" }
  ],
  "pending_generation": null
}
```

- `status = pending / running` 時前端顯示骨架與「Ava 正在比對職缺內容和你的履歷⋯」，「重新分析」按鈕停用。
- `basis.stale = true`：職缺內容（`job.updated_at`）或履歷（換了履歷、重新上傳）在 `analyzed_at` 之後有變動，前端在依據卡片提示「職缺或履歷有更新，建議重新分析」。
- `source`：`initial`（第一次分析）／`more`（使用者按「再多產生」）／`reanalyze`（重新分析時新發現）。前端把最近一次產生的項目標上「新」。
- 沒有履歷時 `basis.resume = null`、`strengths` 為空，`verdict_text` 提示「上傳履歷後可以看到你的優勢與落差」。
- `in_set` 表示這題是否已在題組中，按鈕顯示「已加入題組」。

### `POST /targets/{id}/prep/analyze`（重新分析）

依據卡片上的主按鈕「重新分析」。換履歷時也可以在同一個請求帶入。

```json
// Request（都可省略）
{ "resume_id": "…" }
// 202
{ "generation_id": "…", "status": "pending" }
```

處理：重新讀取職缺完整內容與履歷解析結果 → Prep Analyzer → **更新** `match_score`、`verdict_*`、`strengths`、`gaps`、`directions`、`analyzed_at`；可能題目與準備清單只**附加**和現有項目不重複的新項目（`source = reanalyze`）。同一份 prep 同時只能有一個產生工作（`409`）。

### `POST /preps/{id}/likely-questions/generate`（再多產生可能題目）

「可能被問的題目」旁的「再多產生」按鈕，打開小輸入框（和題庫生成相同的操作方式）。

```json
// Request：只有 note 是使用者打的字，其他有預設值
{ "note": "多一些系統設計和帶人經驗", "category": "系統設計", "count": 3 }
// 202
{ "generation_id": "…", "status": "pending" }
```

| 欄位 | 規則 | 預設 |
|---|---|---|
| `note` | 選填，≤ 300 字 | 空 |
| `category` | 選填：自我介紹、技術深度、系統設計、團隊合作、情境題、動機與規劃 | 不限 |
| `count` | 3 / 5 / 8 | 3 |

產生時會一併讀取職缺內容、履歷、**現有的可能題目與題組題目**（避免重複）。新題目附加在最後（`source = more`）。

### `POST /preps/{id}/checklist/generate`（再多產生準備清單）

```json
// Request
{ "note": "想準備薪資談判和反問面試官的問題", "count": 3 }
// 202
{ "generation_id": "…", "status": "pending" }
```

規則同上（無 `category`）；新項目附加在最後，未勾選。

### `GET /prep-generations/{id}`

```json
{ "id": "…", "kind": "likely_more", "status": "succeeded", "created_count": 3, "skipped_duplicates": 1, "error": null }
```

`kind`：`analyze` / `likely_more` / `checklist_more`。完成後前端重新 `GET /targets/{id}/prep`，並提示「新增了 3 題，原本的都保留」；`created_count = 0` 時提示「沒有新的項目了，換個方向試試」。

### `PATCH /preps/{id}/checklist/{item_id}`

`{ "is_checked": true }` → `204`。前端樂觀更新，失敗才回復。

### `POST /preps/{id}/likely-questions/add-to-set`

```json
// Request：不給 ids 時加入全部尚未加入的
{ "ids": ["…", "…"] }
// 200
{ "added": 2, "skipped_duplicates": 0 }
```

加入的題目寫入該目標職缺的題組（`source = prep`），由 worker 補上 `rubric`；對應的可能題目 `in_set = true`。

> **可能題目和題組的差別**：可能題目是「讀書用的預測題」，附為什麼會問與回答建議，不會直接拿來面試；題組才是模擬面試會問的題目。兩者用「加入題組」連結。

---

## 9. Jobs（職缺庫）

> 職缺庫是**選用的探索功能**（P4）。核心流程只需要「貼上職缺內容」，不依賴職缺庫。

### `GET /jobs`

| Query | 說明 |
|---|---|
| `q` | 關鍵字（職稱、公司、技能） |
| `city` | `台北市` 等 |
| `filters` | 逗號分隔：`target`、`foreign`、`remote`、`hybrid`、`match80` |
| `sort` | `match`（預設）/ `recent` |
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
      "match_score": 92, "target_id": null,
      "posted_at": "2026-10-06T00:00:00Z"
    }
  ],
  "next_cursor": null
}
```

- `match_score` 依主要履歷計算；沒有履歷時為 `null`，前端顯示「上傳履歷看契合度」。
- `target_id` 不為 `null` 表示已是目標職缺。
- 使用者有填 `preferences.desired_roles / desired_locations` 時，預設排序會把符合的職缺排前面。

### `GET /jobs/{id}`

列表欄位，加上 `about`、`duties`、`requirements`。職缺詳情視窗的「練習這個職缺／題組／面試建議」三個按鈕都會先呼叫 `POST /targets`（已是目標職缺則直接使用）。

---

## 10. Interviews（面試，核心）

### 10.1 場次物件

所有面試端點都回傳或內含這個物件：

```json
{
  "id": "…",
  "status": "in_progress",
  "phase": "awaiting_answer",
  "state_version": 7,
  "voice_mode": "scripted",
  "length_mode": "standard",
  "persona": "warm",
  "language": "zh",
  "target_id": "…",
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
  "cues": {
    "acks": [ { "text": "嗯，了解。", "tts_url": "https://…/ack-1.mp3" }, { "text": "謝謝你的分享。", "tts_url": "https://…/ack-2.mp3" } ],
    "last_question_ack": { "text": "好的，我們來聊最後一題。", "tts_url": "https://…/ack-last.mp3" },
    "silence_prompt": { "text": "這題還有要補充的嗎？", "tts_url": "https://…/silence.mp3" },
    "skip_ack": { "text": "沒關係，我們換下一題。", "tts_url": "https://…/skip.mp3" },
    "outro": { "text": "今天的面試到這裡結束，謝謝你。", "tts_url": "https://…/outro.mp3" }
  },
  "limits": { "answer_max_sec": 300, "silence_prompt_sec": 12, "heartbeat_sec": 15 },
  "started_at": "…",
  "elapsed_sec": 214,
  "paused_at": null,
  "resume_deadline": null
}
```

`cues` 是預先合成的接話短句（依語言與風格，跨場次快取），前端在使用者按停止的**同時**播放一句 `acks`，遮住上傳與追問判斷的等待時間，不需要等 API 回應。`realtime` 模式也會帶 `cues`，作為 Realtime 失敗時的備援。

`status` 與 `phase` 決定前端能做什麼：

| status / phase | 前端畫面 | 允許的呼叫 |
|---|---|---|
| `in_progress` / `asking` | Ava 說話中，麥克風停用 | `playback-finished`、`skip`、`pause`、`end` |
| `in_progress` / `awaiting_answer` | 「輪到你了，點麥克風開始回答」 | `answers`、`skip`、`pause`、`end` |
| `in_progress` / `answering` | 錄音中、波形、「重錄」 | `complete`、`discard`、`pause`、`end` |
| `in_progress` / `finalizing` | 上傳中 | （等待 `complete` 回應） |
| `paused` | 「已暫停・計時停止」與「繼續」按鈕 | `resume`、`end` |
| `completed` / `done` | 結束，報告卡片 | `GET report` |

### 10.2 `POST /interviews`（建立場次）

新面試頁按「開始面試」時呼叫。**前端必須先取得麥克風權限才呼叫**（見 [flows.md §10](flows.md)），避免建立了場次才發現沒有麥克風。

```json
// Request
{
  "target_id": "…",
  "resume_id": "…",
  "length_mode": "standard",
  "persona": "warm",
  "language": "zh",
  "recording_consent": true
}
```

處理規則：

1. `recording_consent` 必須為 `true`（「開始面試」按鈕下方的說明即為告知，按下即同意；第一次面試前另外顯示一次說明）。
2. `resume_id` 省略時用目標職缺的預設履歷；可為 `null`（沒有履歷也能練）。
3. 依 `length_mode` 決定主題目數：`quick` 3、`standard` 5、`deep` 8。從題組**依順序取前 N 題**。
4. **題組題數不足時自動補題**（AI 依履歷＋職缺生成，並寫回題組），不讓使用者卡在「題數不足」。
5. 題組仍在生成中：回 `409 RESOURCE_NOT_READY`（前端在生成中本來就會停用按鈕）。
6. `voice_mode` 由後端決定：方案允許且 Realtime 可用時為 `realtime`，否則 `scripted`（MVP 一律 `scripted`）。
7. 在同一交易：建立場次、寫入職缺與履歷快照、複製題目到 `session_questions`、排 TTS 工作（主題目＋開場白；接話短句已有快取就沿用）；把本次的長度／風格／語言寫回 `users.preferences`、更新 `target_jobs.last_practiced_at`。
8. 已有未結束（含暫停中）的場次：回 `409 ACTIVE_SESSION_EXISTS`，`details.session_id` 讓前端顯示「回到那場面試／結束它並開始新的」。
9. 用量超過方案限制回 `403 PLAN_LIMIT_REACHED`。

```json
// 201
{ "session": { …場次物件，status: "preparing", phase: "idle"… },
  "questions": [ { "id": "…", "order_no": 1, "category": "自我介紹" } ] }
```

前端輪詢 `GET /interviews/{id}` 直到 `status = ready`（通常 < 5 秒），期間畫面顯示「Ava 準備中⋯」。題組未修改時 TTS 會重用快取。

### 10.3 `GET /interviews/active`

回傳未結束（`preparing` / `ready` / `in_progress` / `paused`）的場次物件，沒有時回 `204`。與 `GET /me` 的 `active_interview` 相同，供頁面切換時刷新。

### 10.4 `GET /interviews/{id}`

回傳場次物件。用於：頁面重新整理、從其他頁回來繼續、`409` 後重新同步。若 `phase = answering` 且距離 `current_attempt.answer_started_at` 已超過 `answer_max_sec + 30` 秒，後端先將 attempt 標記 `interrupted`、`phase` 退回 `awaiting_answer` 再回傳。

### 10.5 `POST /interviews/{id}/realtime/connect`（只限 `voice_mode = realtime`）

前端建立 `RTCPeerConnection`、加入麥克風 track（先停用）與 data channel、產生 SDP offer，交給後端代為向 Realtime API 建立連線。繼續暫停的面試時也要重新呼叫。

```json
// Request
{ "sdp_offer": "v=0\r\no=- 46117…" }
// 200
{ "sdp_answer": "v=0\r\no=- …", "realtime_call_id": "…", "expires_at": "…" }
```

後端代理 SDP 交換時一併設定 session（前端拿不到金鑰，也不能改這些設定）：

| 設定 | 值 |
|---|---|
| `model` | `REALTIME_MODEL`（例如 `gpt-realtime-2.1`） |
| `turn_detection` | `null`：模型只在前端送 `response.create` 時說話 |
| `input_audio_transcription` | 開啟，用於回答時的即時字幕（只顯示，不評分） |
| `voice` | 與 TTS 相同的聲音（`TTS_VOICE`），讓主題目與接話聲音一致 |
| `instructions` | 面試官角色、語言、風格、「只在被要求時說話、只做被要求的那件事」（見 [architecture.md §6.6](architecture.md)） |

| 錯誤 | 前端處理 |
|---|---|
| `503 REALTIME_UNAVAILABLE` | 後端已把場次 `voice_mode` 改為 `scripted`；前端不建立 WebRTC，改用 `cues` 與 `tts_url`，面試照常進行，只在對話中顯示一行「改用標準語音」 |

> SDP 交換端點與 session 設定格式以 OpenAI 官方文件為準，見 [architecture.md §11](architecture.md)。

### 10.6 `POST /interviews/{id}/start`

```json
// Request
{ "expected_version": 1 }
// 200
{ "session": { …phase: "asking"… },
  "intro": { "text": "你好，品妤！我是今天的面試官 Ava。我們開始吧。", "tts_url": "…" } }
```

前端依序播放 `intro.tts_url` 與 `current_question.tts_url`。

### 10.7 `POST /interviews/{id}/questions/{qid}/playback-finished`

```json
{ "expected_version": 2, "spoken_text": null, "fallback": null }
```

`phase: asking → awaiting_answer`，寫入 `session_questions.asked_at`（**播放紀錄的依據**）。回傳場次物件。

- `spoken_text`：只有 `realtime` 模式的追問需要帶，內容是模型實際說出的話（取自 Realtime 輸出字幕），存到 `session_questions.spoken_text`，評分時一併參考。
- `fallback`：自動播放被瀏覽器擋住時，前端顯示題目文字與「播放」按鈕；使用者看過題目後點麥克風時，前端仍先送 `playback-finished`，並帶 `"text_shown"`，後端記在 `voice_events`。

### 10.8 `POST /interviews/{id}/questions/{qid}/answers`（開始作答）

```json
// Request
{ "expected_version": 3 }
// 201
{ "attempt": { "id": "…", "attempt_no": 1, "answer_started_at": "…" },
  "ack": { "text": "嗯，了解。", "tts_url": "https://…/ack-1.mp3",
           "realtime_instructions": "用一句 10 字以內的話自然回應考生，不評論對錯、不提出新問題。" },
  "session": { …phase: "answering", state_version: 4… } }
```

前端收到後才開麥克風 track 並啟動 `MediaRecorder`。`ack` 是這題回答結束時要說的接話，**事先給好**，讓使用者按停止的瞬間就能播放：

- `scripted`：播放 `ack.tts_url`（後端從 `cues.acks` 挑一句；下一題是最後一題時給 `last_question_ack`）。
- `realtime`：前端先送 `input_audio_buffer.commit`，再送 `response.create`（帶 `ack.realtime_instructions`）。

### 10.9 `POST /interviews/{id}/attempts/{aid}/complete`（上傳回答並取得下一步）

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
2. 音訊上傳物件儲存 → **快速轉錄**（同步，最快的 STT 模型）。
3. 交易寫入 attempt（`saved`、`quick_transcript`）、題目 `answered`、排 `transcribe_attempt`（最終轉錄，評分依據）。
4. 追問 Agent 依快速轉錄判斷（同步，3 秒逾時，逾時視為不追問）。
5. 決定下一步並推進狀態；`scripted` 的追問同步合成 TTS。

```json
// 200
{
  "attempt": { "id": "…", "status": "saved", "audio_duration_ms": 162680,
               "quick_transcript": "之前我們的商品列表頁在資料量大時會很卡⋯" },
  "next": {
    "type": "followup",
    "question": {
      "id": "…", "kind": "followup", "order_no": 2, "followup_no": 1,
      "text": "你剛提到虛擬捲動，為什麼選它而不是分頁？",
      "delivery": "tts",
      "tts_url": "https://…/fu-2-1.mp3",
      "realtime_instructions": null
    }
  },
  "session": { …phase: "asking", state_version: 5… }
}
```

| `next.type` | 意義 | 前端動作 |
|---|---|---|
| `followup` | 追問 | `delivery = tts`：播放 `tts_url`；`delivery = realtime`：送 `response.create`（帶 `realtime_instructions`，內容是「念出以下追問⋯」）。播完送 `playback-finished`（realtime 帶 `spoken_text`） |
| `question` | 下一道主題目 | 等接話播完，再播放新題目的 `tts_url` |
| `completed` | 全部完成 | 播放 `cues.outro`（realtime 可改用 `response.create`），顯示報告卡片（評分中） |

- `attempt.quick_transcript` 用來把對話中的回答泡泡更新成文字（`scripted` 回答時只顯示波形；`realtime` 回答時已有即時字幕，這裡改成較準確的版本）。
- 報告與評分一律使用 worker 產生的最終轉錄，不使用快速轉錄或即時字幕。

**錯誤與重試**：網路失敗或 `5xx` 時，前端保留錄音 Blob，以**同一個** `Idempotency-Key` 指數退避重試（1s、2s、4s，最多 5 次），期間顯示「正在上傳你的回答⋯」；仍失敗時顯示「重新上傳」按鈕，**不得前進到下一題，也不得丟掉錄音**。

### 10.10 `POST /interviews/{id}/attempts/{aid}/discard`（重錄）

```json
{ "expected_version": 4 }
```

只允許 `phase = answering`。前端停止錄音並丟掉 Blob；後端把 attempt 標記 `discarded`、`phase` 退回 `awaiting_answer`，回傳場次物件。使用者再點麥克風即建立 `attempt_no + 1`。不限次數（被丟掉的錄音 24 小時後刪除）。

### 10.11 `POST /interviews/{id}/questions/{qid}/skip`

```json
{ "expected_version": 3 }
```

允許在 `asking` 或 `awaiting_answer`。題目標記 `skipped`，回應格式同 `complete` 的 `next` 與 `session`；前端先播放 `cues.skip_ack`（「沒關係，我們換下一題。」）再播下一題。

### 10.12 `POST /interviews/{id}/pause`、`POST /interviews/{id}/resume`

```json
// pause Request
{ "expected_version": 5, "reason": "user" }
// 200
{ "session": { "status": "paused", "paused_at": "…", "resume_deadline": "…（paused_at + 24h）", … } }
```

- `reason`：`user`（按暫停）/ `page_leave`（離開面試頁，前端在 `visibilitychange`／路由切換時送出）。心跳中斷 2 分鐘時，後端也會自動改為 `paused`（`reason = connection_lost`）。
- 暫停時若正在 `answering`：未送出的錄音丟棄（attempt 標記 `discarded`），回到 `awaiting_answer`。前端暫停畫面會說明。
- 暫停期間不計時（`paused_total_sec` 累加）；`realtime` 模式前端關閉 WebRTC。
- `resume`：`{ "expected_version": 6 }` → `status: in_progress`。若 `asked_at` 已寫入就回到 `awaiting_answer`，否則重新播放目前題目（`asking`）。`voice_mode = realtime` 時前端接著重新呼叫 `realtime/connect`。
- 超過 `resume_deadline` 呼叫 `resume` 回 `409 STATE_CONFLICT`（場次已自動結束，`GET` 會看到 `completed` 或 `aborted`）。

### 10.13 `POST /interviews/{id}/heartbeat`

```json
{ "phase": "answering", "rtc_state": "connected",
  "events": [ { "type": "autoplay_blocked", "at": "…" }, { "type": "realtime_reconnected", "at": "…" } ] }
```

→ `204`。每 15 秒一次；後端更新 `last_heartbeat_at`。`events` 是前端累積的語音事件（播放失敗、自動播放被擋、Realtime 重連或中斷），後端寫入 `voice_events`，用於調查音訊問題。`rtc_state` 只在 `realtime` 模式有值。

### 10.14 `POST /interviews/{id}/end`

前端顯示確認視窗後才呼叫（「已回答的 N 題會產生報告，還沒回答的不會評分」）。

```json
// Request
{ "expected_version": 5, "reason": "user_ended" }
// 200
{ "session": { "status": "completed", "phase": "done", … },
  "report": { "status": "processing" } }
// 沒有任何已作答題目時
{ "session": { "status": "aborted", … }, "report": null }
```

若結束時正在 `answering`，前端先送出 `complete`（保留這題）再呼叫 `end`；若無法完成，該 attempt 標記 `interrupted`，不納入評分。未作答的主題目在報告中顯示「未作答」。`realtime` 模式前端關閉 WebRTC。

---

## 11. Reports（面試報告）

### `GET /interviews`（報告列表）

`?limit=20&cursor=…&q=Northwind`

```json
{
  "summary": { "total": 6, "avg_score": 73, "best_score": 82 },
  "items": [
    { "id": "…", "ended_at": "2026-10-06T06:32:00Z",
      "target_id": "…", "job_label": "Northwind Labs・Senior Frontend Engineer",
      "answered_count": 5, "persona": "warm", "duration_sec": 840,
      "report_status": "ready", "overall_score": 82, "delta_vs_prev": 5 }
  ],
  "next_cursor": null
}
```

- 只列出已結束且有報告的場次；`report_status = processing` 的列顯示「評分中」。
- 前端依 `ended_at` 分組為「最近 7 天」「更早」。
- `delta_vs_prev` 是和**同一個目標職缺**的上一場比較；第一場為 `null`（顯示「首次」）。

### `GET /interviews/{id}/report`

```json
{
  "status": "ready",
  "session": { "id": "…", "ended_at": "2026-10-06T06:32:00Z", "persona": "warm", "language": "zh",
               "target_id": "…", "job_label": "Northwind Labs・Senior Frontend Engineer" },
  "overall_score": 82,
  "delta_vs_prev": 5,
  "summary": "你的回答很有具體數據，這是最大的優勢。下次可以加強系統設計題的結構，並少用「嗯」「應該吧」這類讓人覺得不確定的詞。",
  "dimensions": [
    { "key": "structure",  "label": "內容結構", "hint": "開頭、經過、結果是否清楚", "score": 84 },
    { "key": "depth",      "label": "專業深度", "hint": "技術細節與取捨", "score": 80 },
    { "key": "fluency",    "label": "表達流暢", "hint": "贅詞、停頓與語速", "score": 76 },
    { "key": "job_fit",    "label": "職缺契合", "hint": "回答與職缺需求的關聯", "score": 88 },
    { "key": "confidence", "label": "自信程度", "hint": "用詞是否肯定", "score": 72 }
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

- `status`：`processing` / `ready` / `failed`。`processing` 時前端顯示「Ava 正在逐題轉錄和評分，通常不到一分鐘。完成後會通知你，可以先去做別的事。」與骨架；已完成的題目可先顯示。
- `evaluation.status = needs_review` 時，`review_reason` 說明原因（例如「錄音幾乎沒有聲音」），前端提供「重新評分」。
- `status = skipped` 的題目顯示「已跳過」，沒有 `answer` 與 `evaluation`。
- 總分 = 已作答主題目分數平均（追問分數併入主題目，權重 30%），由程式計算。

### `GET /interviews/{id}/report/events`（SSE）

```
event: snapshot
data: {"evaluated":2,"total":5}

event: evaluation_done
data: {"question_id":"…","score":86}

event: report_ready
data: {"overall_score":82}
```

實作：worker 完成評分或報告時發布到 Redis Pub/Sub `ai:evt:report:{session_id}`；持有 SSE 連線的 API 實例訂閱該頻道後轉送。SSE 建立後先送一次 `snapshot`，避免漏掉訂閱前的事件。

連線最長 120 秒；斷線或 Redis 不可用時改為輪詢 `GET report`。使用者離開報告頁時，前端仍以輕量輪詢 `GET /interviews?limit=1` 偵測完成，並顯示「報告完成了」通知。

### `GET /interviews/{id}/answers/{aid}/audio`

`{ "url": "https://…", "expires_in": 300 }`。音訊超過保留期限回 `410 GONE`（前端隱藏「聽我的錄音」按鈕）。

### `POST /interviews/{id}/report/add-to-set`

```json
// Request：不給 session_question_ids 時，預設加入全部分數 < 80 的題目
{ "session_question_ids": ["…", "…"] }
// 200
{ "target_id": "…", "added": 2, "skipped_duplicates": 0 }
```

加入該場面試所屬目標職缺的題組（`source = report`），並移到題組**最前面**，下次面試優先練。

### `POST /interviews/{id}/questions/{qid}/re-evaluate`

只允許 `evaluation.status in (failed, needs_review)`，每題最多 2 次。`202`。

### `DELETE /interviews/{id}`

前端顯示確認視窗後呼叫。`204`。刪除場次、作答、評分、報告與所有音訊檔。

---

## 12. 前端體驗約定（UX contract）

這些規則讓「最少步驟就能練習」成立，前後端都要遵守：

| 規則 | 說明 |
|---|---|
| 一個主要動作 | 每個畫面只有一個實心主按鈕（新面試頁是「開始面試」；沒有目標職缺時是「貼上職缺內容」） |
| 記住上次設定 | 新面試頁預設選上次練習的目標職缺、履歷、長度、風格、語言（`preferences`、`last_practiced_at`） |
| 不要求先填資料 | 註冊後直接能練；履歷選填；經歷技能從履歷讀，不另外填表 |
| 自動準備 | 加入目標職缺 → 自動出題＋面試建議；題數不足 → 開始時自動補題 |
| 進度可見 | 所有非同步工作都顯示「Ava 正在⋯」與骨架，完成時通知 |
| 可以後悔 | 錄音中可「重錄」；面試可暫停、24 小時內繼續；移出目標職缺不刪資料 |
| 危險操作要確認 | 結束面試、刪除報告、刪除全部紀錄、刪除帳號都要確認視窗，並說明後果 |
| 不讓錯誤擋路 | Realtime 失敗自動改用串接式語音；上傳失敗保留錄音並重試；`409` 先同步狀態，不跳錯誤 |
| 按停止就有回應 | 接話短句事先給好（`cues`、`answers` 回應的 `ack`），按停止的瞬間就播放，不等 API |
| 一次只有一場面試 | 有未結束的面試時，用「繼續／結束並開始新的」選擇取代錯誤訊息 |

---

## 13. Admin

### `POST /admin/jobs/import`

需要 `admin` 角色。Body 為職缺陣列（`company_name`、`title`、`location_text`、`about`、`duties`、`requirements`、`tags`、`external_ref`、`posted_at`、`city`、`work_mode`、`is_foreign`、`work_language`），以 `external_ref` upsert。回 `{ "inserted": 10, "updated": 2 }`，並排程計算 embedding。

---

## 14. 背景工作一覽（非 HTTP，供實作參考）

工作以 arq 執行（Redis broker），並寫入 PostgreSQL `background_jobs` 作 outbox，見 [architecture.md §4.3.3](architecture.md)。

| kind | 佇列 | 觸發 | 結果 |
|---|---|---|---|
| `parse_resume` | default | `POST /resumes` | `resumes.parsed_json`、`analysis_json`、`embedding`；接著排 `compute_matches`；第一份履歷時補到目標職缺並排 `generate_prep` |
| `parse_job` | default | `POST /targets`（貼上 JD） | `job_posts` 欄位、`embedding`；接著排 `generate_questions(initial)` 與 `generate_prep` |
| `compute_matches` | default | 履歷解析完成、設為主要、職缺匯入 | `job_matches`；清除 `ai:cache:jobs:{resume_id}:*` |
| `generate_prep` | default | 加入目標職缺（第一次分析）、換履歷、`prep/analyze` | 更新 `interview_preps` 分析欄位；附加 `prep_likely_questions`、`prep_checklist_items`（不刪除） |
| `extend_prep` | default | `likely-questions/generate`、`checklist/generate` | 附加 `prep_likely_questions` 或 `prep_checklist_items`；`prep_generations` 記錄結果 |
| `generate_questions` | default | 加入目標職缺（`initial`，8 題）、`POST …/questions/generate`、開始面試時題數不足（同步補題） | `question_set_items`；`question_sets.status = ready` |
| `build_rubric` | default | 使用者新增／修改題目、從建議或報告加入題目 | `question_set_items.rubric` |
| `synthesize_tts` | **interactive** | `POST /interviews` | 主題目與開場白 TTS（`session_questions.tts_audio_key`）；接話短句缺快取時一併合成；全部完成後場次改為 `ready` |
| `transcribe_attempt` | **interactive** | `complete` | `answer_attempts.final_transcript`；接著排 `evaluate_attempt` |
| `evaluate_attempt` | default | 轉錄完成 | `evaluations`；發布 `evaluation_done`；若場次已結束且全部完成，排 `build_report` |
| `build_report` | default | 最後一題評分完成（持有 `ai:lock:report:{session_id}`） | `interview_reports`；發布 `report_ready` |

排程工作（arq cron，由 `default` worker 中的一個實例執行）：

| 名稱 | 頻率 | 結果 |
|---|---|---|
| `sweep_outbox` | 每 30 秒 | 重新投遞未投遞或在 Redis 遺失的工作 |
| `pause_lost_sessions` | 每分鐘 | 心跳中斷 2 分鐘的面試改為 `paused`（不是作廢） |
| `expire_paused_sessions` | 每 10 分鐘 | 暫停超過 24 小時的面試自動結束；有作答就排 `build_report` |
| `purge_expired_audio` | 每日 | 刪除超過保留期限的音訊、被重錄丟掉的錄音 |
| `maintain_partitions` | 每日 | 建立下個月分區、刪除過期分區、清除 30 天前完成的工作列 |
