# AI Interview — 各功能系統流程圖

> 版本：v0.2・所有圖皆為 Mermaid 格式・對齊 ChatGPT 風格 UI 原型與 UX 檢查
> 相關文件：[architecture.md](architecture.md)・[api.md](api.md)・[postgresql.md](postgresql.md)

## 目錄

1. [註冊與登入](#1-註冊與登入)
2. [Token 自動更新](#2-token-自動更新)
3. [履歷上傳與 AI 解析](#3-履歷上傳與-ai-解析)
4. [設定（偏好、履歷、帳號）](#4-設定偏好履歷帳號)
5. [職缺庫搜尋與契合度](#5-職缺庫搜尋與契合度)
6. [加入目標職缺（自動出題＋面試建議）](#6-加入目標職缺自動出題面試建議)
7. [面試建議](#7-面試建議)
8. [題目生成（Question Planner Agent）](#8-題目生成question-planner-agent)
9. [題目編輯與排序](#9-題目編輯與排序)
10. [開始面試（麥克風權限＋快照＋TTS）](#10-開始面試麥克風權限快照tts)
11. [即時語音面試：連線](#11-即時語音面試連線)
12. [即時語音面試：逐題主流程](#12-即時語音面試逐題主流程)
13. [面試狀態機](#13-面試狀態機)
14. [前端音訊與麥克風閘門](#14-前端音訊與麥克風閘門)
15. [追問決策](#15-追問決策)
16. [回答上傳失敗與重試](#16-回答上傳失敗與重試)
17. [暫停、離開、斷線與繼續](#17-暫停離開斷線與繼續)
18. [跳過、重錄與結束面試](#18-跳過重錄與結束面試)
19. [評分管線（轉錄 → 逐題評分 → 報告）](#19-評分管線轉錄--逐題評分--報告)
20. [面試報告：列表與詳情](#20-面試報告列表與詳情)
21. [新使用者上手流程](#21-新使用者上手流程)
22. [背景工作生命週期](#22-背景工作生命週期)
23. [AI 呼叫與 Log 記錄](#23-ai-呼叫與-log-記錄)
24. [使用者完整旅程](#24-使用者完整旅程)

---

## 1. 註冊與登入

```mermaid
sequenceDiagram
    autonumber
    actor U as 使用者
    participant FE as 前端
    participant API as FastAPI
    participant DB as PostgreSQL

    alt Email 註冊
        U->>FE: 填寫 Email、密碼、名稱
        FE->>API: POST /auth/register
        API->>DB: 檢查 email 是否存在
        API->>DB: INSERT users、user_profiles
    else Email 登入
        U->>FE: 輸入 Email、密碼
        FE->>API: POST /auth/login
        API->>DB: 查 users，argon2 驗證
        API->>DB: 失敗次數 +1（超過 5 次鎖 15 分）
    else Google 登入
        FE->>API: GET /auth/google/start
        API-->>FE: 302 Google 授權頁
        U->>FE: 同意授權
        FE->>API: GET /auth/google/callback?code=…
        API->>DB: 查 oauth_identities，沒有就建立 users
    end
    API->>DB: INSERT refresh_tokens（雜湊）
    API->>DB: INSERT app_events auth.login
    API-->>FE: access_token + Set-Cookie refresh_token
    FE->>FE: access_token 存記憶體，導向新面試頁（不要求先填個人資料）
```

## 2. Token 自動更新

```mermaid
flowchart TD
    A["api.js 發出請求"] --> B{"回應 401 TOKEN_EXPIRED？"}
    B -- 否 --> Z["回傳結果"]
    B -- 是 --> C{"已有 refresh 進行中？"}
    C -- 是 --> D["等待同一個 refresh Promise"]
    C -- 否 --> E["POST /auth/refresh（帶 Cookie）"]
    E --> F{"成功？"}
    F -- 是 --> G["更新記憶體 access_token"]
    G --> H["重送原請求一次"]
    D --> H
    H --> Z
    F -- 否 --> I["清除狀態，導向登入頁"]
```

## 3. 履歷上傳與 AI 解析

```mermaid
sequenceDiagram
    autonumber
    actor U as 使用者
    participant FE as 前端（個人檔案）
    participant API as FastAPI
    participant OBJ as 物件儲存
    participant DB as PostgreSQL
    participant W as Worker
    participant AI as OpenAI

    U->>FE: 拖曳或選擇 PDF／Word
    FE->>FE: 檢查副檔名與 ≤ 10 MB
    FE->>API: POST /resumes（multipart）
    API->>API: 檢查 MIME、計算 sha256
    alt 相同檔案已上傳過
        API-->>FE: 200 既有履歷
    else 新檔案
        API->>OBJ: PUT users/{uid}/resumes/{id}
        API->>DB: INSERT resumes（pending）＋ background_jobs parse_resume（同一交易）
        API-->>FE: 202 parse_status=pending
    end
    FE->>FE: 顯示「Ava 正在解析內容」
    W->>DB: 取出 parse_resume
    W->>OBJ: 下載原檔
    W->>W: pdfplumber / python-docx 抽文字
    alt 抽不到文字（掃描檔）
        W->>AI: 以視覺模型讀取頁面
    end
    W->>AI: Resume Parser Agent（Structured Outputs）
    AI-->>W: parsed_json + analysis_json
    W->>W: Pydantic 驗證
    W->>AI: Embedding
    W->>DB: UPDATE resumes（parsed, embedding）
    W->>DB: 排 compute_matches
    loop 每 2 秒
        FE->>API: GET /resumes/{id}
    end
    API-->>FE: parsed + analysis（履歷亮點／可以更好）
```

## 4. 設定（偏好、履歷、帳號）

不提供經歷、技能、手機等表單；個人資料以履歷為準。

```mermaid
flowchart LR
    subgraph 設定視窗
        A["一般：主題、預設長度／語言／風格"] -->|"PATCH /me preferences"| U[("users.preferences")]
        B["履歷：上傳、設為主要、刪除"] -->|"POST / PATCH / DELETE /resumes"| R[("resumes")]
        C["求職意向（選填）：職位、地點"] -->|"PATCH /me preferences"| U
        D["帳號：名稱、用量、刪除紀錄／帳號"] -->|"PATCH /me、DELETE /me/interviews、DELETE /me"| X[("users 與所有資料")]
    end
    R --> P["GET /resumes/{id}：唯讀顯示「Ava 從履歷讀到的」經歷、技能、亮點"]
    N["新面試頁每次開始面試"] -->|"自動寫回長度／風格／語言"| U
    U --> Q["下次打開新面試頁的預設值"]
```

## 5. 職缺搜尋與契合度

```mermaid
sequenceDiagram
    autonumber
    actor U as 使用者
    participant FE as 前端（職缺找尋）
    participant API as FastAPI
    participant DB as PostgreSQL
    participant W as Worker
    participant AI as OpenAI

    Note over W,DB: 背景：契合度預先計算
    W->>DB: 取出 compute_matches（resume_id）
    W->>DB: pgvector 以 cosine 找最相近的 200 個職缺
    W->>W: 相似度換算 0–100，加入規則加減分（年資、城市、遠端偏好）
    W->>DB: UPSERT job_matches

    U->>FE: 輸入關鍵字、地區、快速篩選
    FE->>API: GET /jobs?q=&city=&filters=&sort=match
    API->>DB: job_posts（trigram 搜尋）LEFT JOIN job_matches、target_jobs
    API-->>FE: 職缺列表（含 match_score、target_id）
    U->>FE: 點選職缺
    FE->>API: GET /jobs/{id}
    API-->>FE: 詳情視窗（關於、工作內容、條件）
    U->>FE: 練習這個職缺／題組／面試建議
    FE->>API: POST /targets { job_post_id }（已是目標職缺則直接使用）
    API-->>FE: 202 目標職缺（題組 preparing）
    FE->>FE: 前往新面試頁／題組頁／面試建議頁，顯示「Ava 正在出題⋯」
```

## 6. 加入目標職缺（自動出題＋面試建議）

使用者只做一件事：貼上職缺（或在職缺庫點「練習這個職缺」）。題組與面試建議由系統自動準備。

```mermaid
sequenceDiagram
    autonumber
    actor U as 使用者
    participant FE as 前端
    participant API as FastAPI
    participant DB as PostgreSQL
    participant W as Worker
    participant AI as OpenAI

    U->>FE: 「貼上新職缺」視窗貼上內容，按「加入目標職缺」
    FE->>API: POST /targets { raw_text }
    API->>API: 基本檢查（長度、是否有職稱字樣）
    alt 看不出是職缺
        API-->>FE: 422 JD_UNREADABLE
        FE->>U: 「至少要有職稱和工作內容」
    else 可以解析
        API->>DB: 交易：INSERT job_posts（source=user, pending）、target_jobs、question_sets（preparing）＋ outbox parse_job
        API-->>FE: 202 目標職缺物件
        FE->>U: 自動選好這個職缺；「本次題目」顯示「Ava 正在依職缺和履歷出題⋯」，開始按鈕暫時停用
        W->>AI: JD Parser
        W->>DB: UPDATE job_posts（parsed）＋ embedding
        par 自動準備
            W->>AI: Question Planner（initial，8 題，涵蓋各題型）
            W->>DB: INSERT question_set_items、question_sets.status=ready
        and
            W->>AI: Prep Analyzer
            W->>DB: INSERT interview_preps、prep_checklist_items
        end
        loop 每 2 秒（直到 question_set.status = ready）
            FE->>API: GET /targets/{id}
        end
        FE->>U: 題目預覽出現，「開始面試」可按；toast「Ava 已幫你準備 8 題」
    end
```

```mermaid
flowchart TD
    A{"從哪裡加入？"} -->|"貼上職缺內容"| B["POST /targets { raw_text }"]
    A -->|"職缺庫：練習這個職缺／題組／面試建議"| C["POST /targets { job_post_id }"]
    B --> D["parse_job"]
    D --> E{"解析成功？"}
    E -- 否 --> E1["重試一次；仍失敗：job_posts.parse_status=failed，目標職缺顯示「請修正職缺內容」與編輯表單（PATCH /targets）"]
    E -- 是 --> F["generate_questions(initial) ＋ generate_prep"]
    C --> F
    F --> G["題組 ready → 新面試可開始；面試建議頁有內容"]
    H{"已經是目標職缺？"} -.-> C
    H -- 曾移出 --> I["status 改回 active，找回原本的題組與報告"]
```

## 7. 面試建議

面試建議在加入目標職缺時就自動產生，打開頁面就有內容。

```mermaid
sequenceDiagram
    autonumber
    actor U as 使用者
    participant FE as 前端（面試建議）
    participant API as FastAPI
    participant DB as PostgreSQL
    participant W as Worker
    participant AI as OpenAI

    U->>FE: 打開面試建議（預設選上次練習的目標職缺）
    FE->>API: GET /targets/{id}/prep
    alt status = ready
        API-->>FE: 契合度、優勢、補強、方向、可能題目、清單
    else pending / running
        API-->>FE: 進度
        FE->>U: 骨架＋「Ava 正在比對你的履歷和職缺⋯」，完成後自動顯示
    end
    opt 按「重新生成」或換了履歷
        FE->>API: POST /targets/{id}/prep/regenerate
        W->>AI: Prep Analyzer
        W->>DB: 新的 interview_preps（勾選狀態依相同文字沿用）
    end
    U->>FE: 勾選準備清單
    FE->>API: PATCH /preps/{id}/checklist/{item_id}（樂觀更新）
    U->>FE: 「可能題目加入題組」
    FE->>API: POST /preps/{id}/likely-questions/add
    API->>DB: INSERT question_set_items source=prep（去重）＋ 排 build_rubric
    API-->>FE: added / skipped_duplicates
    U->>FE: 「開始模擬面試」
    FE->>FE: 前往新面試頁（職缺已選好）
```

## 8. 題目生成（Question Planner Agent）

```mermaid
flowchart TD
    A0["觸發：加入目標職缺（initial）／題庫生成輸入框／開始面試時題數不足"] --> A["參數：題型、難度、題數、使用者輸入的需求（都有預設值）"]
    A --> B["POST /targets/{id}/questions/generate"]
    B --> C{"該題組已有生成工作進行中？"}
    C -- 是 --> C1["409"]
    C -- 否 --> D["INSERT question_generations pending ＋ 工作 generate_questions"]
    D --> E["Worker 組裝輸入"]

    subgraph 輸入組裝
        E1["職缺：職稱、工作內容、條件、標籤"]
        E2["履歷：經歷、專案、量化成果、技能（沒有履歷則略過）"]
        E3["面試建議中的「需要補強」（若有）"]
        E4["題組既有題目（避免重複）"]
        E5["參數：題型、難度、題數、補充需求"]
    end
    E --> E1 & E2 & E3 & E4 & E5

    E1 & E2 & E3 & E4 & E5 --> F["LLM：Question Planner（Structured Outputs）"]
    F --> G["輸出 count + 3 題候選，每題含：類型、難度、題目、考察能力、預期要點、評分規準、出題理由"]
    G --> H["程式檢查"]

    subgraph 程式檢查
        H1["題型都在選定範圍內"]
        H2["長度 ≤ 120 字"]
        H3["與既有題目 trigram 相似度 > 0.6 剔除"]
        H4["候選之間互相去重"]
        H5["每個選定題型至少 1 題"]
    end
    H --> H1 --> H2 --> H3 --> H4 --> H5

    H5 --> I{"合格題數 ≥ count？"}
    I -- 是 --> J["取前 count 題，附加到題組最後"]
    I -- 否 --> K{"已重試？"}
    K -- 否 --> F
    K -- 是 --> L["寫入現有合格題目，status=succeeded，回報實際數量"]
    J --> M["question_generations succeeded"]
    L --> M
    M --> N["前端輪詢後重新載入題組，toast「新增了 N 題」"]
```

## 9. 題目編輯與排序

```mermaid
flowchart TD
    A["題組畫面"] --> B{"使用者動作"}
    A0["題庫生成：先選目標職缺"] --> A
    B -- 上移／下移 --> C["前端交換陣列"] --> C1["PUT /targets/{id}/questions/order"]
    C1 --> C2["交易內批次更新 order_no（可延遲唯一約束）"]
    B -- 編輯 --> D["PATCH items/{item_id}"] --> D1{"text 有變？"}
    D1 -- 是 --> D2["排 build_rubric 重新產生評分規準"]
    D1 -- 否 --> D3["只更新 category / difficulty"]
    B -- 新增 --> E["POST items（source=user）"] --> D2
    B -- 刪除 --> F["DELETE items/{item_id}"] --> F1["軟刪除＋剩餘題目重新排號"]
    B -- 用這組題目面試 --> G["前往新面試頁（職缺已選好）"]
```

## 10. 開始面試（麥克風權限＋快照＋TTS）

```mermaid
sequenceDiagram
    autonumber
    actor U as 使用者
    participant FE as 前端（新面試）
    participant API as FastAPI
    participant DB as PostgreSQL
    participant W as Worker
    participant AI as OpenAI
    participant OBJ as 物件儲存

    Note over FE: 預設帶入上次的職缺、履歷、長度、風格、語言
    U->>FE: 按「開始面試」
    alt 有暫停中的面試
        FE->>U: 「回到那場面試／結束它並開始新的」
    end
    FE->>FE: getUserMedia 取得麥克風權限（第一次會顯示說明）
    alt 權限被拒或沒有麥克風
        FE->>U: 說明如何在瀏覽器開啟麥克風，不建立場次
    else 取得權限
        FE->>API: POST /interviews { target_id, length_mode, persona, language, recording_consent }
        API->>DB: 檢查方案用量、未結束場次、題組狀態
        API->>DB: 讀取題組前 N 題
        alt 題數不足
            API->>AI: Question Planner 補題（同步，逾時 15 秒）
            API->>DB: 補的題目寫回題組
        end
        API->>DB: 交易：INSERT interview_sessions（快照）、session_questions × N、outbox synthesize_tts；寫回 preferences、last_practiced_at
        API-->>FE: 201 場次＋題目清單
        FE->>U: 對話區顯示「Ava 準備中⋯」
        W->>OBJ: TTS 快取命中就沿用，否則 TTS 後上傳
        W->>DB: 全部完成 → status=ready
        FE->>API: GET /interviews/{id}（輪詢到 ready）→ live/connect → start
    end
```

## 11. 即時語音面試：連線

```mermaid
sequenceDiagram
    autonumber
    participant FE as 前端 rtc.js
    participant API as FastAPI
    participant SB as Sideband 管理器
    participant GL as GPT-Live
    participant DB as PostgreSQL
    participant RD as Redis

    FE->>FE: new RTCPeerConnection，加入麥克風 track（先 disabled）
    FE->>FE: 建立 data channel，遠端音軌接到 GainNode（gain=0）
    FE->>FE: createOffer → setLocalDescription
    FE->>API: POST /interviews/{id}/live/connect { sdp_offer }
    API->>GL: 以後端金鑰提交 SDP offer
    alt 成功
        GL-->>API: sdp_answer ＋ 對話識別碼
        API->>DB: UPDATE interview_sessions live_session_ref
        API->>RD: SET ai:live:owner:{session_id} = instance_id（NX，TTL 30 秒）
        API->>SB: 開啟 sideband WebSocket，訂閱 ai:live:cmd:{session_id}
        SB->>GL: 系統指示（角色、風格、語言、禁止自行出題）
        SB->>RD: XADD ai:live:events connected
        API-->>FE: 200 sdp_answer
        FE->>FE: setRemoteDescription，連線建立
    else 失敗
        API->>DB: voice_mode=scripted，voice_mode_degraded_at=now()
        API-->>FE: 503 LIVE_UNAVAILABLE
        FE->>FE: 提示改用標準語音模式，不建立 WebRTC
    end
    FE->>API: POST /interviews/{id}/start
```

## 12. 即時語音面試：逐題主流程

這是整個產品最關鍵的流程。主題目由**應用程式播放**，GPT‑Live 只在被允許的時段發聲。

```mermaid
sequenceDiagram
    autonumber
    actor U as 使用者
    participant FE as 前端控制器
    participant API as FastAPI 狀態機
    participant DB as PostgreSQL
    participant SB as Sideband
    participant GL as GPT-Live
    participant RD as Redis
    participant OBJ as 物件儲存
    participant W as Worker

    Note over FE,API: phase = asking
    API->>SB: set_question_context(Q)：題目、考察重點、「安靜聆聽」
    FE->>FE: 麥克風 disabled、AI 閘門關閉
    FE->>U: 播放 Q 的 TTS，畫面打字顯示題目
    FE->>API: POST questions/Q/playback-finished
    API->>DB: asked_at=now()，phase=awaiting_answer
    FE->>U: 「輪到你了，點麥克風開始回答」

    U->>FE: 點麥克風
    FE->>API: POST questions/Q/answers
    API->>DB: INSERT answer_attempts A，phase=answering
    API-->>FE: attempt A
    FE->>FE: 麥克風 enabled，MediaRecorder.start()
    U->>GL: 語音回答（WebRTC）
    GL-->>FE: 即時字幕（data channel）→ 右側逐字稿
    GL-->>SB: 即時字幕
    SB->>RD: RPUSH ai:live:tr:{A}；XADD ai:live:events

    opt 靜音超過 12 秒
        FE->>FE: 短暫打開 AI 閘門
        GL-->>U: 「這題還有要補充的嗎？」
    end

    U->>FE: 再點麥克風（回答完畢）
    FE->>FE: MediaRecorder.stop() 取得 Blob，麥克風 disabled
    FE->>FE: 打開 AI 閘門（短回應窗口 3 秒）
    API->>SB: allow_reaction()
    GL-->>U: 「嗯，了解，謝謝你的分享。」
    FE->>API: POST attempts/A/complete（audio、Idempotency-Key）
    API->>RD: 冪等檢查 ai:idem:{uid}:{key}（SET processing）
    API->>OBJ: 上傳音訊
    API->>RD: 取出 ai:live:tr:{A} 拼成 live_transcript
    API->>DB: 交易：attempt saved、Q answered、寫 outbox transcribe_attempt
    API->>RD: 投遞 arq interactive 佇列
    API->>API: Follow-up Decider（≤ 3 秒）
    API->>DB: 推進到追問或下一題，state_version+1
    API->>RD: 冪等結果存回（TTL 24 小時）
    API-->>FE: next
    FE->>FE: 關閉 AI 閘門

    par 背景
        RD->>W: transcribe_attempt → evaluate_attempt
        W->>DB: 寫入逐字稿與評分
    end

    alt next.type = followup
        API->>SB: speak_followup(F)
        FE->>FE: 打開 AI 閘門
        GL-->>U: 念出追問
        SB->>DB: 記錄 spoken_text
        FE->>API: playback-finished(F)
    else next.type = question
        Note over FE: 回到開頭，播放下一題 TTS
    else next.type = completed
        FE->>U: 播放結尾語，導向報告頁
    end
```

## 13. 面試狀態機

### 13.1 場次生命週期（`status`）

```mermaid
stateDiagram-v2
    [*] --> preparing: POST /interviews
    preparing --> ready: TTS 全部完成
    preparing --> aborted: TTS 失敗且重試用盡
    ready --> in_progress: POST start
    ready --> aborted: 30 分鐘未開始
    in_progress --> paused: pause（按暫停／離開頁面）或心跳中斷 2 分鐘
    paused --> in_progress: resume（24 小時內）
    paused --> completed: 超過 24 小時且有作答
    paused --> aborted: 超過 24 小時且沒有作答
    paused --> completed: end（有作答）
    in_progress --> completed: 最後一題完成
    in_progress --> completed: end（有作答）
    in_progress --> aborted: end（沒有作答）
    completed --> [*]
    aborted --> [*]
```

### 13.2 題目階段（`phase`）

```mermaid
stateDiagram-v2
    [*] --> idle
    idle --> asking: start（第 1 題）
    asking --> awaiting_answer: playback-finished
    asking --> asking: skip（換下一題）
    awaiting_answer --> answering: answers（點麥克風）
    awaiting_answer --> asking: skip
    answering --> finalizing: complete 請求進入處理
    answering --> awaiting_answer: discard（重錄）或 pause
    finalizing --> answering: 上傳或入庫失敗
    finalizing --> asking: 成功，有追問或下一題
    finalizing --> done: 成功，沒有下一題
    answering --> awaiting_answer: 重連時 attempt 逾時，標記 interrupted
    asking --> done: end
    awaiting_answer --> done: end
    answering --> done: end（attempt 標記 interrupted）
    done --> [*]
```

### 13.3 作答紀錄（`answer_attempts.status`）

```mermaid
stateDiagram-v2
    [*] --> answering: 開始作答
    answering --> saved: 音訊與紀錄入庫
    answering --> discarded: 使用者按「重錄」或暫停
    answering --> interrupted: 斷線逾時、提前結束
    answering --> failed: 上傳重試用盡
    saved --> [*]: is_final = true，進入評分
    discarded --> [*]: 不評分，24 小時後刪除錄音
    interrupted --> [*]: 不評分
    failed --> [*]: 不評分
```

## 14. 前端音訊與麥克風閘門

```mermaid
flowchart TD
    subgraph 前端音訊路由
        MIC["麥克風 MediaStream"] --> T1["track 給 WebRTC（enabled 依 phase）"]
        MIC --> T2["clone track 給 MediaRecorder（逐題錄音）"]
        REMOTE["GPT-Live 遠端音軌"] --> GAIN["GainNode（閘門）"] --> SPK["喇叭"]
        TTS["主題目 TTS 音訊"] --> SPK
    end

    P{"目前狀態"}
    P -->|"asking（播放主題目）"| S1["麥克風 OFF・閘門 OFF・播放 TTS"]
    P -->|"awaiting_answer"| S2["麥克風 OFF・閘門 OFF"]
    P -->|"answering"| S3["麥克風 ON・閘門 OFF・錄音中"]
    P -->|"回答結束後 3 秒"| S4["麥克風 OFF・閘門 ON（短回應）"]
    P -->|"靜音提醒"| S5["麥克風 ON・閘門 ON 一次"]
    P -->|"追問播放（live_voice）"| S6["麥克風 OFF・閘門 ON"]
    P -->|"結尾語"| S7["麥克風 OFF・閘門 ON"]

    S1 & S2 & S3 --> X{"sideband 偵測到模型在閘門關閉時輸出語音？"}
    X -- 是 --> Y["使用者聽不到；記錄 live_events output_while_gated；送取消回應"]
```

## 15. 追問決策

```mermaid
flowchart TD
    A["complete：回答已入庫"] --> B{"這是主題目？"}
    B -- 否（已是追問） --> B1{"該主題目追問數 < 上限？"}
    B -- 是 --> C
    B1 -- 否 --> Z["不追問 → 下一道主題目"]
    B1 -- 是 --> C{"風格上限：warm 1／real 1／tough 2"}
    C --> D{"live_transcript 有內容？"}
    D -- 否 --> D1["用剛上傳音訊做快速轉錄（逾時 2 秒）"]
    D1 --> E
    D -- 是 --> E["Follow-up Decider：題目、規準、逐字稿、風格、已追問次數"]
    E --> F{"3 秒內回應？"}
    F -- 否 --> Z
    F -- 是 --> G{"should_follow_up？"}
    G -- 否 --> Z
    G -- 是 --> H["程式檢查：長度 ≤ 80 字、不是新主題、與主題目相關"]
    H -- 不通過 --> Z
    H -- 通過 --> I["INSERT session_questions kind=followup、parent_id、followup_no+1"]
    I --> J{"voice_mode"}
    J -- live --> K["delivery=live_voice，sideband 要求 GPT-Live 念出"]
    J -- scripted --> L["即時 TTS（~1 秒），delivery=tts"]
```

## 16. 回答上傳失敗與重試

```mermaid
flowchart TD
    A["MediaRecorder.stop() → Blob"] --> B["產生 Idempotency-Key（存在 sessionStorage）"]
    B --> C["POST complete"]
    C --> D{"結果"}
    D -- 200 --> E["清除暫存，處理 next"]
    D -->|"409 STATE_CONFLICT"| F["GET /interviews/{id}"]
    F --> F1{"attempt 已是 saved？"}
    F1 -- 是 --> E
    F1 -- 否 --> G["依伺服器狀態重設畫面"]
    D -->|"網路錯誤／5xx"| H{"重試次數 < 5？"}
    H -- 是 --> I["指數退避 1s、2s、4s…，相同 Idempotency-Key"] --> C
    H -- 否 --> J["顯示「上傳失敗，重新上傳」，停在原題"]
    J --> K["使用者點重新上傳"] --> C
    D -->|"413／415"| L["提示重答本題（新 attempt）"]

    subgraph 伺服器端冪等
        S1["收到 complete"] --> S2{"Redis ai:idem 有這個 key？"}
        S2 -->|"done"| S3["回傳上次結果，不重複寫入"]
        S2 -->|"processing"| S5["409 REQUEST_IN_PROGRESS，前端稍後重試"]
        S2 -->|"沒有"| S6["SET NX 寫入 processing"]
        S2 -->|"Redis 不可用"| S7{"answer_attempts.idempotency_key 已存在？"}
        S7 -- 是 --> S3
        S7 -- 否 --> S4
        S6 --> S4["正常處理，完成後把結果存回 Redis"]
    end
```

## 17. 暫停、離開、斷線與繼續

任何中斷都進入「暫停」，24 小時內都能回來，不讓使用者白練。

```mermaid
flowchart TD
    A{"發生什麼"} --> B["按「暫停」"]
    A --> C["離開面試頁（點側欄、切到別頁）"]
    A --> D["重新整理／網路短斷"]
    A --> E["心跳中斷 2 分鐘"]
    A --> F["WebRTC 斷線"]

    B & C --> P1{"正在錄音？"}
    P1 -- 是 --> P2["丟掉未送出的錄音（attempt discarded），回到「輪到你了」"]
    P1 -- 否 --> P3
    P2 --> P3["POST /pause（reason=user／page_leave）：status=paused，停止計時，關閉 GPT-Live"]
    E --> P3
    P3 --> P4["側欄顯示「面試暫停中・繼續」；新面試頁顯示「繼續面試／結束並產生報告」"]

    D --> D1["GET /interviews/{id}"]
    D1 --> D2{"phase"}
    D2 -->|"asking / awaiting_answer"| D3["重新播放目前題目"]
    D2 -->|"answering"| D4{"本機還有錄音 Blob？"}
    D4 -- 是 --> D5["補送 complete"]
    D4 -- 否 --> D6["attempt 標記 interrupted，回到「輪到你了」"]
    D2 -->|"done"| D7["顯示報告卡片"]

    F --> F1["重新 live/connect 一次"]
    F1 --> F2{"成功？"}
    F2 -- 是 --> F3["sideband 重送目前題目上下文，繼續"]
    F2 -- 否 --> F4["降級 scripted：只用 TTS＋錄音，狀態機不變；對話中顯示「改用標準語音」"]

    P4 --> R{"使用者回來？"}
    R -->|"24 小時內按「繼續」"| R1["POST /resume → live/connect；對話插入「已回到面試・從第 N 題繼續」"]
    R1 --> R2{"目前題目已播放？"}
    R2 -- 是 --> R3["回到「輪到你了」"]
    R2 -- 否 --> R4["重新播放目前題目"]
    R -->|"超過 24 小時"| X{"有作答？"}
    X -- 有 --> X1["自動結束（expired），產生報告"]
    X -- 沒有 --> X2["aborted，不產生報告"]
```

## 18. 跳過、重錄與結束面試

```mermaid
flowchart TD
    A["按「跳過」（輪到你回答時）"] --> B["POST questions/{qid}/skip"]
    B --> D["session_questions.status=skipped，報告顯示「已跳過」"]
    D --> E["Ava：「沒關係，我們換下一題。」→ 下一題或結束"]

    R["錄音中按「重錄」"] --> R1["前端停止錄音、丟掉 Blob"]
    R1 --> R2["POST attempts/{aid}/discard"]
    R2 --> R3["attempt=discarded，phase 回到 awaiting_answer"]
    R3 --> R4["使用者再按麥克風 → attempt_no+1（不限次數，只評最後送出的那次）"]

    X["按「結束面試」"] --> Y["確認視窗：已回答 N 題會產生報告，未回答的 M 題不會評分"]
    Y -- 繼續面試 --> Y0["關閉視窗"]
    Y -- 結束 --> Z0{"正在錄音？"}
    Z0 -- 是 --> Z1["先送 complete 保留這題"]
    Z0 -- 否 --> Z
    Z1 --> Z["POST /interviews/{id}/end"]
    Z --> Z2{"有任何作答？"}
    Z2 -- 有 --> Z3["status=completed，關閉 sideband 與 WebRTC，前往報告詳情（評分中）"]
    Z2 -- 沒有 --> Z4["status=aborted，不產生報告，回到新面試頁"]
    Z3 --> Z5{"所有已存回答都評分完成？"}
    Z5 -- 是 --> Z6["立即排 build_report"]
    Z5 -- 否 --> Z7["等最後一個 evaluate_attempt 完成時排 build_report"]
```

## 19. 評分管線（轉錄 → 逐題評分 → 報告）

```mermaid
flowchart TD
    A["complete 成功"] --> B["transcribe_attempt（優先序 10）"]
    B --> C["從物件儲存下載該題音訊"]
    C --> D["STT 檔案轉錄（含時間段）"]
    D --> E{"品質檢查"}
    E -->|"無聲音／字數 < 10"| E1["transcript_status=low_quality"]
    E -- 正常 --> E2["final_transcript、transcript_segments"]
    E1 --> F
    E2 --> F["evaluate_attempt"]

    F --> G{"transcript_status"}
    G -- low_quality --> G1["evaluations.status=needs_review，review_reason=回答過短或聽不清楚"]
    G -- done --> H["組裝輸入：題目、評分規準、預期要點、職缺摘要、相關履歷背景、完整逐字稿、追問與回答"]
    H --> I["Answer Evaluator（Structured Outputs，temperature 低）"]
    I --> J["程式驗證"]

    subgraph 程式驗證
        J1["分數 0–100、五維度齊全"]
        J2["evidence 每句都必須出現在逐字稿中，否則剔除"]
        J3["highlight 必須是逐字稿子字串，否則改用第一條 evidence"]
        J4["strengths ≤ 3、improvements ≤ 4"]
    end
    J --> J1 --> J2 --> J3 --> J4

    J4 --> K["程式計算：贅詞次數、語速"]
    K --> L["INSERT evaluations status=done"]
    G1 --> M
    L --> M{"場次已結束 且 所有 final attempt 都評分完成？"}
    M -- 否 --> M1["等待"]
    M -- 是 --> N["build_report（dedupe_key＋Redis 鎖 ai:lock:report:{session_id}）"]
    N --> O["程式計算總分、五維度平均、平均作答時間、與上次差距"]
    O --> P["Report Aggregator：總結評語、下次練習重點"]
    P --> Q["interview_reports status=ready"]
    L --> L1["PUBLISH ai:evt:report:{session_id} evaluation_done"]
    Q --> R["PUBLISH report_ready"]
    R --> R1["持有 SSE 的 API 實例轉送給前端"]
```

## 20. 面試報告：列表與詳情

```mermaid
sequenceDiagram
    autonumber
    actor U as 使用者
    participant FE as 前端（面試報告）
    participant API as FastAPI
    participant DB as PostgreSQL
    participant RD as Redis
    participant W as Worker
    participant OBJ as 物件儲存

    U->>FE: 點側欄「面試報告」
    FE->>API: GET /interviews
    API->>DB: 已結束且有報告的場次＋彙總
    API-->>FE: 共 N 場、平均、最高；每列分數與比上次（評分中的列顯示「評分中」）
    FE->>U: 依時間分組（最近 7 天／更早），可搜尋職缺或公司
    U->>FE: 點一列
    FE->>API: GET /interviews/{id}/report
    alt status = processing
        API-->>FE: 已完成題目＋其餘骨架
        FE->>U: 「Ava 正在逐題轉錄和評分，通常不到一分鐘，完成後會通知你」
        FE->>API: GET /report/events（SSE）
        API->>RD: SUBSCRIBE ai:evt:report:{session_id}
        API-->>FE: event snapshot（目前進度）
        W->>RD: PUBLISH evaluation_done × N、report_ready
        API-->>FE: evaluation_done × N、report_ready
        FE->>API: GET /interviews/{id}/report
    end
    API-->>FE: 總分、比上次、總評、五個面向、逐題回顧
    U->>FE: 「聽我的錄音」
    FE->>API: GET /answers/{aid}/audio
    API->>OBJ: 5 分鐘預簽名 URL
    U->>FE: 「弱項加入題組」
    FE->>API: POST /report/add-to-set
    API->>DB: 分數 < 80 的題目加到該目標職缺題組最前面（source=report，去重）
    U->>FE: 「再練一次」
    FE->>FE: 前往新面試頁（同一個目標職缺已選好）
    opt 刪除報告
        U->>FE: 更多 → 刪除這份報告 → 確認
        FE->>API: DELETE /interviews/{id}
    end
```

## 21. 新使用者上手流程

目標：從註冊到開始第一場面試 **< 3 分鐘**，只需要貼上一段職缺內容。

```mermaid
flowchart TD
    A["註冊／Google 登入"] --> B["GET /me：onboarding = { has_resume: false, has_target: false }"]
    B --> C["新面試頁（引導狀態）：「先告訴 Ava 你想應徵哪個職缺」"]
    C --> D["主按鈕：「貼上職缺內容」；次要：「或從職缺庫挑選」"]
    C --> E["履歷列：「上傳履歷」（選填：沒有履歷也能練，題目只依職缺出）"]
    D --> F["POST /targets → 自動出題＋面試建議（約 10 秒，顯示進度）"]
    E -.-> E1["POST /resumes → 解析完成後補到目標職缺並更新面試建議"]
    F --> G["題目預覽出現，「開始面試」可按（長度／風格／語言已有預設值）"]
    G --> H["開始面試 → 麥克風權限說明 → 第一題"]
    H --> I["完成 → 報告卡片（評分中 → 完成通知）"]
    I --> J["報告：逐題建議 → 弱項加入題組 → 再練一次"]
```

| 畫面狀態 | 判斷依據 | 顯示 |
|---|---|---|
| 沒有目標職缺 | `onboarding.has_target = false` | 標題「先告訴 Ava 你想應徵哪個職缺」，主按鈕「貼上職缺內容」，開始按鈕停用並說明原因 |
| 題組出題中 | `question_set.status = preparing` | 題目預覽骨架＋「Ava 正在依職缺和履歷出題⋯」，開始按鈕停用 |
| 沒有履歷 | `onboarding.has_resume = false` | 履歷列顯示「上傳履歷」與「選填」說明；面試建議頁提示上傳後可看到優勢與落差 |
| 有暫停中的面試 | `active_interview != null` | 橫幅「你有一場暫停中的面試」：繼續面試／結束並產生報告 |
| 沒有任何報告 | 報告列表為空 | 「完成第一場面試後，逐題評分和改善建議會出現在這裡」＋「開始第一場面試」 |

## 22. 背景工作生命週期

佇列在 Redis（arq），PostgreSQL `background_jobs` 是 outbox 與執行紀錄。完整投遞時序見 [architecture.md §4.3.3](architecture.md)。

```mermaid
stateDiagram-v2
    [*] --> queued: INSERT（與業務資料同一交易）
    queued --> queued: COMMIT 後投遞 arq，寫 dispatched_at
    queued --> running: Worker 取得，條件更新成功
    running --> succeeded: handler 成功
    running --> retrying: 失敗且 attempts < max，arq Retry 指數退避
    retrying --> running: 重試時間到
    running --> dead: 失敗且 attempts ≥ max
    running --> retrying: started_at 超過 10 分鐘（卡住回收）
    succeeded --> [*]
    dead --> [*]: 告警，人工處理
```

```mermaid
flowchart LR
    subgraph PG["PostgreSQL"]
        OB[("background_jobs outbox")]
    end
    subgraph RD["Redis"]
        QI["arq:queue:interactive"]
        QD["arq:queue:default"]
    end
    API["FastAPI"] -->|"交易內寫入"| OB
    API -->|"COMMIT 後投遞"| QI & QD
    SW["sweep_outbox 每 30 秒"] -->|"未投遞或遺失"| OB
    SW -->|"重新投遞（相同 _job_id）"| QI & QD
    QI --> WI["interactive worker：TTS、轉錄"]
    QD --> WD["default worker：解析、生成、評分、報告、排程"]
    WI & WD -->|"更新狀態"| OB
```

```mermaid
flowchart LR
    T0["POST /targets"] --> J
    R["parse_resume"] --> M["compute_matches"]
    J["parse_job"] --> M
    J --> Q["generate_questions（initial）"]
    J --> P["generate_prep"]
    TQ["synthesize_tts"] --> RDY["場次 ready"]
    T["transcribe_attempt"] --> EV["evaluate_attempt"]
    EV --> RP["build_report（全部完成時）"]
    BR["build_rubric"]
    CRON["arq cron：sweep_outbox／flush_live_events／pause_lost_sessions／expire_paused_sessions／purge_expired_audio／maintain_partitions"]
```

## 23. AI 呼叫與 Log 記錄

```mermaid
sequenceDiagram
    autonumber
    participant SVC as 任一 Agent / Worker
    participant LC as LLMClient
    participant AI as OpenAI
    participant DB as PostgreSQL
    participant LOG as stdout / Sentry

    SVC->>LC: call(purpose, model, prompt_version, schema, user_id, session_id)
    LC->>LC: 記錄開始時間、request_id
    LC->>AI: 請求（逾時、最多重試 2 次）
    alt 成功
        AI-->>LC: 結果＋用量
        LC->>LC: 依 Structured Outputs schema 解析
        LC->>DB: INSERT llm_calls（tokens、audio_seconds、cost_usd、latency_ms、status=ok）
        LC-->>SVC: 已驗證的物件
    else 失敗或逾時
        LC->>DB: INSERT llm_calls（status=error/timeout、error_code）
        LC->>LOG: 結構化錯誤 log＋Sentry
        LC-->>SVC: 拋出 UpstreamError（由工作重試機制處理）
    end
```

## 24. 使用者完整旅程

```mermaid
flowchart TD
    A["註冊／登入"] --> C{"有目標職缺？"}
    C -- 沒有 --> D["貼上職缺內容（或從職缺庫挑選）"]
    D --> E["Ava 自動出題＋產生面試建議"]
    C -- 有 --> F["新面試頁：上次的設定已選好"]
    E --> F
    B["上傳履歷（選填）"] -.-> E
    F --> G{"想先準備？"}
    G -- 看面試建議 --> P["面試建議：優勢、補強、可能題目 → 加入題組"]
    G -- 調整題目 --> Q["題庫生成：請 Ava 出題、編輯、排序"]
    P --> F
    Q --> F
    G -- 直接開始 --> H["面試：逐題播放、回答、可重錄／跳過／暫停"]
    H --> I["Ava 逐題評分（評分中 → 完成通知）"]
    I --> J["報告：逐題原題、逐字稿、聽錄音、做得好、改善建議、可以這樣說"]
    J --> K{"下一步"}
    K -- 弱項加入題組 --> F
    K -- 再練一次 --> F
    K -- 換職缺 --> C
```

