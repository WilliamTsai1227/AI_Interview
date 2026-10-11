-- 0001 初始結構：與 spec/postgresql.md §4 一致（由規格產生，之後的變更請新增 0002_*.sql）

CREATE EXTENSION IF NOT EXISTS citext;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS vector;     -- pgvector
-- gen_random_uuid() 自 PostgreSQL 13 起內建

CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger AS $$
BEGIN NEW.updated_at = now(); RETURN NEW; END $$ LANGUAGE plpgsql;
-- 每張含 updated_at 的表都掛：
-- CREATE TRIGGER trg_<table>_updated BEFORE UPDATE ON <table> FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE users (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email              citext NOT NULL UNIQUE,
  password_hash      text,                                   -- 僅 OAuth 登入者為 NULL
  display_name       text NOT NULL,
  avatar_key         text,
  plan               text NOT NULL DEFAULT 'free' CHECK (plan IN ('free','pro')),
  role               text NOT NULL DEFAULT 'user' CHECK (role IN ('user','admin')),
  status             text NOT NULL DEFAULT 'active' CHECK (status IN ('active','suspended','deleted')),
  locale             text NOT NULL DEFAULT 'zh-TW',
  preferences        jsonb NOT NULL DEFAULT '{}',              -- 見 §5；新面試頁的預設值
  email_verified_at  timestamptz,
  last_login_at      timestamptz,
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE oauth_identities (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id           uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  provider          text NOT NULL CHECK (provider IN ('google')),
  provider_user_id  text NOT NULL,
  created_at        timestamptz NOT NULL DEFAULT now(),
  UNIQUE (provider, provider_user_id)
);

CREATE TABLE refresh_tokens (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  family_id   uuid NOT NULL,                 -- 同一登入鏈；偵測到重放時整個 family 撤銷
  token_hash  text NOT NULL UNIQUE,          -- SHA-256，不存明文
  expires_at  timestamptz NOT NULL,
  revoked_at  timestamptz,
  user_agent  text,
  ip          inet,
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_refresh_tokens_user ON refresh_tokens(user_id) WHERE revoked_at IS NULL;

-- 不另外建 user_profiles / user_skills / work_experiences：
-- 經歷、技能、成果一律從履歷解析（resumes.parsed_json），使用者不用重複填寫；
-- 名稱在 users.display_name，偏好與求職意向在 users.preferences。

-- 直接上傳：API 只發「上傳單據」與預簽名網址，檔案不經過 API
CREATE TABLE upload_tickets (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id       uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  purpose       text NOT NULL CHECK (purpose IN ('resume','answer_audio')),
  object_key    text NOT NULL UNIQUE,         -- users/{uid}/uploads/{id}
  content_type  text NOT NULL,
  max_bytes     bigint NOT NULL,
  attempt_id    uuid,                         -- purpose = answer_audio 時對應的作答（FK 於 §4.7 補上）
  status        text NOT NULL DEFAULT 'issued' CHECK (status IN ('issued','uploaded','consumed','expired')),
  expires_at    timestamptz NOT NULL,         -- 網址 5 分鐘有效；單據 1 小時內沒使用就清除物件
  consumed_at   timestamptz,
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_upload_tickets_cleanup ON upload_tickets(expires_at) WHERE status IN ('issued','uploaded');

CREATE TABLE resumes (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id        uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  filename       text NOT NULL,
  mime_type      text NOT NULL,
  size_bytes     int  NOT NULL CHECK (size_bytes <= 10 * 1024 * 1024),
  file_key       text NOT NULL,              -- users/{user_id}/resumes/{id}.pdf
  sha256         text NOT NULL,
  is_primary     boolean NOT NULL DEFAULT false,
  parse_status   text NOT NULL DEFAULT 'pending'
                 CHECK (parse_status IN ('pending','parsing','parsed','failed')),
  parsed_text    text,
  parsed_json    jsonb,                      -- 基本資料、經歷、技能、專案、量化成果
  analysis_json  jsonb,                      -- {highlights:[], improvements:[]}
  embedding      vector(1536),               -- 維度依 EMBEDDING_MODEL 調整
  parse_error    text,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now(),
  deleted_at     timestamptz
);
CREATE INDEX ix_resumes_user ON resumes(user_id, created_at DESC) WHERE deleted_at IS NULL;
CREATE UNIQUE INDEX uq_resumes_primary ON resumes(user_id) WHERE is_primary AND deleted_at IS NULL;

-- 職能分類（可分層，例如 engineering > backend）；ETL 與篩選都用這張表
CREATE TABLE job_categories (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  code        text NOT NULL UNIQUE,             -- backend、frontend、data、pm、design…
  label       text NOT NULL,                    -- 後端工程
  parent_id   uuid REFERENCES job_categories(id),
  sort_order  int NOT NULL DEFAULT 0,
  is_active   boolean NOT NULL DEFAULT true
);

-- 每次職缺 ETL 的查詢條件與結果統計
CREATE TABLE job_ingest_runs (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  query            jsonb NOT NULL,              -- {category:"backend", region:"台北市", foreign:true, language:"en"}
  status           text NOT NULL DEFAULT 'running' CHECK (status IN ('running','succeeded','failed')),
  fetched_count    int NOT NULL DEFAULT 0,      -- 搜尋抓回
  valid_count      int NOT NULL DEFAULT 0,      -- 通過程式驗證
  published_count  int NOT NULL DEFAULT 0,      -- 自動上架
  review_count     int NOT NULL DEFAULT 0,      -- 送審
  closed_count     int NOT NULL DEFAULT 0,      -- 定期檢查下架
  cost_usd         numeric(10,4),
  error            text,
  started_at       timestamptz NOT NULL DEFAULT now(),
  finished_at      timestamptz
);

CREATE TABLE job_posts (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source           text NOT NULL CHECK (source IN ('catalog','user')),
  owner_user_id    uuid REFERENCES users(id) ON DELETE CASCADE,
  external_ref     text,                     -- 匯入來源的 ID
  company_name     text NOT NULL,
  company_logo_key text,
  title            text NOT NULL,
  city             text,                     -- 台北市、新竹市…（篩選用）
  location_text    text,                     -- 台北市信義區
  employment_type  text,                     -- 全職、兼職…
  work_mode        text CHECK (work_mode IN ('onsite','hybrid','remote')),
  work_mode_note   text,                     -- 每週 2 天遠端
  salary_text      text,
  salary_min       int,
  salary_max       int,
  salary_period    text CHECK (salary_period IN ('month','year')),
  tags             text[] NOT NULL DEFAULT '{}',
  about            text,                     -- 使用者貼上職缺時的「關於」
  summary          text,                     -- 平台職缺：Ava 寫的 2–3 句摘要（不轉貼原文）
  duties           jsonb NOT NULL DEFAULT '[]',   -- 平台職缺為 Ava 改寫的重點條列
  requirements     jsonb NOT NULL DEFAULT '[]',
  category_id      uuid REFERENCES job_categories(id),
  seniority        text CHECK (seniority IN ('junior','mid','senior','lead')),
  source_site      text,                     -- 104、cake、linkedin、yourator、greenhouse、lever、company…
  source_url       text,                     -- 原始職缺頁（詳情的「前往原始職缺頁」）
  source_url_norm  text,                     -- 正規化網址（去除追蹤參數），用來去重
  ingest_run_id    uuid REFERENCES job_ingest_runs(id) ON DELETE SET NULL,
  fetched_at       timestamptz,
  last_verified_at timestamptz,              -- 最近一次確認原始網址仍有效
  ai_confidence    numeric(3,2),             -- 分類與內容的信心分數 0–1
  review_status    text NOT NULL DEFAULT 'published'
                   CHECK (review_status IN ('published','pending_review','rejected')),
  review_reasons   jsonb NOT NULL DEFAULT '[]',   -- ["分類信心低","公司名稱與網域不一致"]
  work_language    text,                     -- zh / en
  is_foreign       boolean NOT NULL DEFAULT false,
  raw_text         text,                     -- 使用者貼上的原始 JD
  parse_status     text NOT NULL DEFAULT 'parsed'
                   CHECK (parse_status IN ('pending','parsing','parsed','failed')),
  search_text      text NOT NULL DEFAULT '', -- 職稱+公司+標籤，應用程式維護
  embedding        vector(1536),
  status           text NOT NULL DEFAULT 'active' CHECK (status IN ('draft','active','closed')),
  posted_at        timestamptz,
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now(),
  deleted_at       timestamptz,
  CHECK ((source = 'user') = (owner_user_id IS NOT NULL)),
  CHECK (source <> 'catalog' OR source_url IS NOT NULL)    -- 平台職缺一定要有原始職缺頁
);
CREATE UNIQUE INDEX uq_job_posts_source ON job_posts(source_site, external_ref) WHERE external_ref IS NOT NULL;
CREATE UNIQUE INDEX uq_job_posts_url    ON job_posts(source_url_norm) WHERE source_url_norm IS NOT NULL AND deleted_at IS NULL;
CREATE INDEX ix_job_posts_review   ON job_posts(review_status, fetched_at) WHERE review_status = 'pending_review';
CREATE INDEX ix_job_posts_verify   ON job_posts(last_verified_at) WHERE source = 'catalog' AND status = 'active';
CREATE INDEX ix_job_posts_category ON job_posts(category_id, seniority) WHERE status = 'active' AND review_status = 'published';
CREATE INDEX ix_job_posts_catalog ON job_posts(status, posted_at DESC) WHERE source = 'catalog' AND deleted_at IS NULL;
CREATE INDEX ix_job_posts_owner   ON job_posts(owner_user_id, created_at DESC) WHERE source = 'user' AND deleted_at IS NULL;
CREATE INDEX ix_job_posts_city    ON job_posts(city);
CREATE INDEX ix_job_posts_search  ON job_posts USING gin (search_text gin_trgm_ops);
CREATE INDEX ix_job_posts_tags    ON job_posts USING gin (tags);
CREATE INDEX ix_job_posts_emb     ON job_posts USING hnsw (embedding vector_cosine_ops);

-- 目標職缺：使用者正在準備的職缺。加入時系統自動建立題組並產生面試建議
CREATE TABLE target_jobs (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id            uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  job_post_id        uuid NOT NULL REFERENCES job_posts(id) ON DELETE CASCADE,
  resume_id          uuid REFERENCES resumes(id) ON DELETE SET NULL,   -- 這個職缺預設用的履歷，可為空（沒有履歷也能練）
  status             text NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived')),
  last_practiced_at  timestamptz,
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now(),
  UNIQUE (user_id, job_post_id)
);
CREATE INDEX ix_target_jobs_user ON target_jobs(user_id, last_practiced_at DESC NULLS LAST) WHERE status = 'active';
-- 「移出目標職缺」= status 改為 archived；題組與報告都保留，重新加入就能找回

CREATE TABLE job_matches (
  resume_id    uuid NOT NULL REFERENCES resumes(id) ON DELETE CASCADE,
  job_post_id  uuid NOT NULL REFERENCES job_posts(id) ON DELETE CASCADE,
  user_id      uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  score        smallint NOT NULL CHECK (score BETWEEN 0 AND 100),
  method       text NOT NULL CHECK (method IN ('embedding','llm')),
  rationale    jsonb,
  computed_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (resume_id, job_post_id)
);
CREATE INDEX ix_job_matches_rank ON job_matches(resume_id, score DESC);

CREATE TABLE interview_preps (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id           uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  target_job_id     uuid NOT NULL UNIQUE REFERENCES target_jobs(id) ON DELETE CASCADE,   -- 每個目標職缺一份；重新分析時更新這一列
  resume_id         uuid REFERENCES resumes(id),          -- 上次分析用的履歷；沒有履歷時只依職缺分析
  job_post_id       uuid NOT NULL REFERENCES job_posts(id),
  status            text NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','running','ready','failed')),
  match_score       smallint CHECK (match_score BETWEEN 0 AND 100),
  verdict_title     text,                    -- 整體很適合你
  verdict_text      text,
  strengths         jsonb,                   -- ["3 年 React 經驗…"]
  gaps              jsonb,                   -- ["履歷沒有寫到帶人…"]
  directions        jsonb,                   -- [{"label":"React 效能優化","probability":92}]
  analyzed_at       timestamptz,             -- 上次分析完成時間（依據卡片顯示）
  job_updated_at    timestamptz,             -- 分析時職缺的 updated_at；之後職缺有改就提示重新分析
  model             text,
  prompt_version    text,
  error             text,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now()
);

-- 「重新分析」與「再多產生」的工作紀錄（同一份 prep 同時只能有一個進行中）
CREATE TABLE prep_generations (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  prep_id        uuid NOT NULL REFERENCES interview_preps(id) ON DELETE CASCADE,
  kind           text NOT NULL CHECK (kind IN ('analyze','likely_more','checklist_more')),
  params         jsonb NOT NULL DEFAULT '{}',   -- {note, category, count, resume_id}
  status         text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','running','succeeded','failed')),
  created_count  int NOT NULL DEFAULT 0,
  model          text,
  prompt_version text,
  error          text,
  created_at     timestamptz NOT NULL DEFAULT now(),
  finished_at    timestamptz
);
CREATE UNIQUE INDEX uq_prep_generation_running ON prep_generations(prep_id) WHERE status IN ('pending','running');

-- 可能被問的題目：只附加，不刪除
CREATE TABLE prep_likely_questions (
  id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  prep_id               uuid NOT NULL REFERENCES interview_preps(id) ON DELETE CASCADE,
  generation_id         uuid REFERENCES prep_generations(id) ON DELETE SET NULL,
  order_no              int NOT NULL,
  question              text NOT NULL,
  why                   text,
  tip                   text,
  priority              text NOT NULL CHECK (priority IN ('high','medium','low')),
  source                text NOT NULL CHECK (source IN ('initial','more','reanalyze')),
  question_set_item_id  uuid,                          -- 已加入題組；FK 於 §4.6 建立題組表後補上
  created_at            timestamptz NOT NULL DEFAULT now(),
  UNIQUE (prep_id, order_no)
);
CREATE INDEX ix_prep_likely_text_trgm ON prep_likely_questions USING gin (question gin_trgm_ops);  -- 產生時去重

CREATE TABLE prep_checklist_items (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  prep_id        uuid NOT NULL REFERENCES interview_preps(id) ON DELETE CASCADE,
  generation_id  uuid REFERENCES prep_generations(id) ON DELETE SET NULL,
  text           text NOT NULL,
  is_checked     boolean NOT NULL DEFAULT false,
  sort_order     int NOT NULL,
  source         text NOT NULL DEFAULT 'initial' CHECK (source IN ('initial','more','reanalyze')),
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_prep_checklist ON prep_checklist_items(prep_id, sort_order);

-- 題組：每個目標職缺剛好一組（MVP 不支援一個職缺多組，介面與 API 都比較單純）
CREATE TABLE question_sets (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id        uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  target_job_id  uuid NOT NULL UNIQUE REFERENCES target_jobs(id) ON DELETE CASCADE,
  status         text NOT NULL DEFAULT 'preparing' CHECK (status IN ('preparing','ready')),  -- 加入目標職缺後自動出第一組題目
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE question_generations (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id             uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  set_id              uuid NOT NULL REFERENCES question_sets(id) ON DELETE CASCADE,
  params              jsonb NOT NULL,        -- {categories:[], difficulty, count, note}
  status              text NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending','running','succeeded','failed')),
  created_item_count  int NOT NULL DEFAULT 0,
  model               text,
  prompt_version      text,
  error               text,
  created_at          timestamptz NOT NULL DEFAULT now(),
  finished_at         timestamptz
);

CREATE TABLE question_set_items (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  set_id           uuid NOT NULL REFERENCES question_sets(id) ON DELETE CASCADE,
  generation_id    uuid REFERENCES question_generations(id) ON DELETE SET NULL,
  order_no         int  NOT NULL,
  category         text NOT NULL,            -- 自我介紹、技術深度、系統設計、團隊合作、情境題、動機與規劃、自訂
  difficulty       text NOT NULL CHECK (difficulty IN ('basic','medium','advanced')),
  text             text NOT NULL CHECK (length(text) BETWEEN 1 AND 500),
  competency       text,                     -- 考察能力
  expected_points  jsonb NOT NULL DEFAULT '[]',
  rubric           jsonb,                    -- 評分規準，使用者自訂題目時由 AI 補
  source           text NOT NULL CHECK (source IN ('ai','user','prep','report')),
  source_reason    text,                     -- 為何針對此職缺／履歷出這題
  followup_mode    text NOT NULL DEFAULT 'adaptive'
                   CHECK (followup_mode IN ('none','fixed','planned','adaptive')),
  followup_count   smallint NOT NULL DEFAULT 1,   -- fixed：固定次數（1–3）；adaptive：上限（1–2）；planned 與 none 不使用
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now(),
  deleted_at       timestamptz,
  CHECK (followup_mode <> 'fixed'    OR followup_count BETWEEN 1 AND 3),
  CHECK (followup_mode <> 'adaptive' OR followup_count BETWEEN 1 AND 2)
);
-- 排序用可延遲唯一約束，交換順序時同一交易內不會撞號
ALTER TABLE question_set_items
  ADD CONSTRAINT uq_set_items_order UNIQUE (set_id, order_no) DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX ix_set_items_text_trgm ON question_set_items USING gin (text gin_trgm_ops);

-- 預設追問（followup_mode = planned 時，依 order_no 依序提問）
CREATE TABLE question_set_followups (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  item_id     uuid NOT NULL REFERENCES question_set_items(id) ON DELETE CASCADE,
  order_no    smallint NOT NULL CHECK (order_no BETWEEN 1 AND 5),
  text        text NOT NULL CHECK (length(text) BETWEEN 1 AND 200),
  rubric      jsonb,                         -- worker 補上
  source      text NOT NULL DEFAULT 'user' CHECK (source IN ('user','ai')),
  created_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (item_id, order_no) DEFERRABLE INITIALLY DEFERRED
);

ALTER TABLE prep_likely_questions
  ADD CONSTRAINT fk_prep_likely_set_item FOREIGN KEY (question_set_item_id) REFERENCES question_set_items(id) ON DELETE SET NULL;

CREATE TABLE interview_sessions (
  id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id                 uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  target_job_id           uuid NOT NULL REFERENCES target_jobs(id),
  job_post_id             uuid NOT NULL REFERENCES job_posts(id),
  resume_id               uuid REFERENCES resumes(id),       -- 沒有履歷也能練
  question_set_id         uuid REFERENCES question_sets(id) ON DELETE SET NULL,
  length_mode             text NOT NULL CHECK (length_mode IN ('quick','standard','deep')),
  persona                 text NOT NULL CHECK (persona IN ('warm','real','tough')),
  language                text NOT NULL CHECK (language IN ('zh','en','mixed')),
  voice_mode              text NOT NULL CHECK (voice_mode IN ('scripted','realtime')),  -- MVP 一律 scripted；realtime 為 P3
  voice_mode_degraded_at  timestamptz,       -- realtime 降級為 scripted 的時間
  status                  text NOT NULL DEFAULT 'preparing'
                          CHECK (status IN ('preparing','ready','in_progress','paused','completed','aborted')),
  phase                   text NOT NULL DEFAULT 'idle'
                          CHECK (phase IN ('idle','asking','awaiting_answer','answering','finalizing','done')),
  current_question_id     uuid,              -- FK 於下方補上（循環參照）
  current_attempt_id      uuid,
  state_version           int  NOT NULL DEFAULT 0,
  job_snapshot            jsonb NOT NULL,
  resume_snapshot         jsonb NOT NULL,    -- 只放出題與評分需要的結構化欄位
  planned_question_count  smallint NOT NULL,
  realtime_call_id        text,              -- realtime 模式最近一次 WebRTC 連線的識別碼（除錯用）
  recording_consent_at    timestamptz,
  last_heartbeat_at       timestamptz,
  paused_at               timestamptz,       -- 使用者按暫停、離開頁面或斷線超過 2 分鐘；24 小時內可繼續
  paused_total_sec        int NOT NULL DEFAULT 0,  -- 暫停時間不計入作答時間
  started_at              timestamptz,
  ended_at                timestamptz,
  end_reason              text CHECK (end_reason IN ('completed','user_ended','expired','error')),  -- expired：暫停超過 24 小時
  duration_sec            int,
  created_at              timestamptz NOT NULL DEFAULT now(),
  updated_at              timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_sessions_user_recent ON interview_sessions(user_id, created_at DESC);
CREATE INDEX ix_sessions_active ON interview_sessions(last_heartbeat_at) WHERE status = 'in_progress';
CREATE INDEX ix_sessions_paused ON interview_sessions(paused_at) WHERE status = 'paused';
-- 同一使用者同時間只能有一場未結束的面試（前端會顯示「繼續／結束」而不是報錯）
CREATE UNIQUE INDEX uq_sessions_one_active ON interview_sessions(user_id) WHERE status IN ('preparing','ready','in_progress','paused');

CREATE TABLE session_questions (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id       uuid NOT NULL REFERENCES interview_sessions(id) ON DELETE CASCADE,
  parent_id        uuid REFERENCES session_questions(id) ON DELETE CASCADE,
  source_item_id   uuid REFERENCES question_set_items(id) ON DELETE SET NULL,
  kind             text NOT NULL CHECK (kind IN ('main','followup')),
  order_no         int  NOT NULL,            -- 主題目順序；追問沿用主題目的 order_no
  followup_no      int  NOT NULL DEFAULT 0,  -- 主題目 0，追問 1、2…
  followup_mode    text CHECK (followup_mode IN ('none','fixed','planned','adaptive')),  -- 只在主題目：開始面試時的快照
  followup_target  smallint,                 -- 只在主題目：fixed＝要問的次數；planned＝預設追問數；adaptive＝上限
  followup_source  text CHECK (followup_source IN ('fixed_ai','fixed_fallback','planned','adaptive_ai')),  -- 只在追問
  source_followup_id uuid REFERENCES question_set_followups(id) ON DELETE SET NULL,  -- planned 追問的來源
  category         text NOT NULL,
  difficulty       text NOT NULL,
  text             text NOT NULL,            -- 建立後不可修改
  spoken_text      text,                     -- realtime 模式追問由模型念出時的實際內容（取自輸出字幕）
  competency       text,
  expected_points  jsonb NOT NULL DEFAULT '[]',
  rubric           jsonb,
  tts_audio_key    text,
  tts_status       text NOT NULL DEFAULT 'pending' CHECK (tts_status IN ('pending','ready','failed','not_needed')),
  status           text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','asked','answered','skipped')),
  asked_at         timestamptz,
  created_at       timestamptz NOT NULL DEFAULT now(),
  UNIQUE (session_id, order_no, followup_no),
  CHECK ((kind = 'main') = (parent_id IS NULL)),
  CHECK ((kind = 'main') = (followup_no = 0)),
  CHECK ((kind = 'main') = (followup_mode IS NOT NULL)),
  CHECK ((kind = 'followup') = (followup_source IS NOT NULL))
);
-- 計算「這道主題目已問幾個追問」用
CREATE INDEX ix_session_questions_parent ON session_questions(parent_id) WHERE parent_id IS NOT NULL;

CREATE TABLE answer_attempts (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id           uuid NOT NULL REFERENCES interview_sessions(id) ON DELETE CASCADE,
  session_question_id  uuid NOT NULL REFERENCES session_questions(id) ON DELETE CASCADE,
  attempt_no           smallint NOT NULL,
  is_final             boolean NOT NULL DEFAULT false,
  status               text NOT NULL DEFAULT 'answering'
                       CHECK (status IN ('answering','saved','discarded','interrupted','failed')),  -- discarded：使用者按「重錄」
  answer_started_at    timestamptz NOT NULL,
  answer_ended_at      timestamptz,
  audio_key            text,                 -- users/{uid}/sessions/{sid}/answers/{attempt_id}.webm
  audio_mime           text,
  audio_bytes          int,
  audio_duration_ms    int,
  quick_transcript     text,                 -- 送出回答時同步產生的快速轉錄，供追問判斷與畫面顯示（不用於評分）
  final_transcript     text,                 -- 從完整錄音轉錄，評分依據
  transcript_segments  jsonb,                -- [{start_ms,end_ms,text}]
  transcript_status    text NOT NULL DEFAULT 'pending'
                       CHECK (transcript_status IN ('pending','done','failed','low_quality')),
  stt_model            text,
  idempotency_key      text UNIQUE,
  upload_ticket_id     uuid REFERENCES upload_tickets(id) ON DELETE SET NULL,   -- 錄音的上傳單據
  created_at           timestamptz NOT NULL DEFAULT now(),
  updated_at           timestamptz NOT NULL DEFAULT now(),
  UNIQUE (session_question_id, attempt_no)
);
ALTER TABLE upload_tickets
  ADD CONSTRAINT fk_upload_tickets_attempt FOREIGN KEY (attempt_id) REFERENCES answer_attempts(id) ON DELETE CASCADE;
CREATE UNIQUE INDEX uq_attempts_final ON answer_attempts(session_question_id) WHERE is_final;
CREATE INDEX ix_attempts_session ON answer_attempts(session_id);

ALTER TABLE interview_sessions
  ADD CONSTRAINT fk_sessions_current_question FOREIGN KEY (current_question_id) REFERENCES session_questions(id) DEFERRABLE INITIALLY DEFERRED,
  ADD CONSTRAINT fk_sessions_current_attempt  FOREIGN KEY (current_attempt_id)  REFERENCES answer_attempts(id)  DEFERRABLE INITIALLY DEFERRED;

CREATE TABLE evaluations (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  attempt_id           uuid NOT NULL UNIQUE REFERENCES answer_attempts(id) ON DELETE CASCADE,
  session_id           uuid NOT NULL REFERENCES interview_sessions(id) ON DELETE CASCADE,
  session_question_id  uuid NOT NULL REFERENCES session_questions(id) ON DELETE CASCADE,
  status               text NOT NULL DEFAULT 'pending'
                       CHECK (status IN ('pending','running','done','failed','needs_review')),
  score                smallint CHECK (score BETWEEN 0 AND 100),
  dimensions           jsonb,                -- {structure,depth,fluency,job_fit,confidence} 各 0-100
  evidence             jsonb,                -- 逐字稿原句陣列（已驗證存在）
  strengths            jsonb,
  improvements         jsonb,
  better_answer        text,                 -- 「可以這樣說」
  highlight            text,                 -- 逐字稿中要標示的句子
  filler_count         int,
  filler_terms         jsonb,                -- {"嗯":3,"應該吧":1}
  speech_rate          numeric(6,1),         -- 字/分 或 詞/分
  review_reason        text,                 -- needs_review 的原因
  rubric_version       text,
  prompt_version       text,
  model                text,
  error                text,
  created_at           timestamptz NOT NULL DEFAULT now(),
  finished_at          timestamptz
);
CREATE INDEX ix_evaluations_session ON evaluations(session_id, status);

CREATE TABLE interview_reports (
  session_id        uuid PRIMARY KEY REFERENCES interview_sessions(id) ON DELETE CASCADE,
  user_id           uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  status            text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','processing','ready','failed')),
  overall_score     smallint CHECK (overall_score BETWEEN 0 AND 100),
  prev_session_id   uuid REFERENCES interview_sessions(id) ON DELETE SET NULL,
  delta_vs_prev     smallint,
  dimension_scores  jsonb,                   -- 五維度平均
  summary           text,
  focus_areas       jsonb,                   -- 下次練習優先順序
  filler_count      int,
  avg_answer_sec    int,
  speech_rate       numeric(6,1),
  answered_count    smallint,
  skipped_count     smallint,
  model             text,
  error             text,
  created_at        timestamptz NOT NULL DEFAULT now(),
  finished_at       timestamptz
);
CREATE INDEX ix_reports_user_trend ON interview_reports(user_id, created_at DESC) WHERE status = 'ready';

-- 每道主題目的整組分數：主題目與每個追問各自在 evaluations 評分，這裡存合併結果
CREATE TABLE question_group_scores (
  main_question_id  uuid PRIMARY KEY REFERENCES session_questions(id) ON DELETE CASCADE,  -- kind = main
  session_id        uuid NOT NULL REFERENCES interview_sessions(id) ON DELETE CASCADE,
  main_score        smallint,
  followup_avg      smallint,                 -- 沒有追問時為 NULL
  followup_count    smallint NOT NULL DEFAULT 0,
  group_score       smallint NOT NULL CHECK (group_score BETWEEN 0 AND 100),
  weights           jsonb NOT NULL DEFAULT '{"main":0.6,"followups":0.4}',
  created_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_group_scores_session ON question_group_scores(session_id);

CREATE TABLE background_jobs (
  id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,   -- 也當作 arq 的 _job_id
  kind           text NOT NULL,              -- parse_resume, generate_questions, synthesize_tts, transcribe_attempt, evaluate_attempt, build_report…
  queue          text NOT NULL DEFAULT 'default' CHECK (queue IN ('interactive','default')),
  payload        jsonb NOT NULL,
  status         text NOT NULL DEFAULT 'queued'
                 CHECK (status IN ('queued','running','retrying','succeeded','dead')),
  run_at         timestamptz NOT NULL DEFAULT now(),  -- 延遲執行（arq _defer_until）
  dispatched_at  timestamptz,                -- 最近一次投遞到 Redis 的時間
  attempts       smallint NOT NULL DEFAULT 0,
  max_attempts   smallint NOT NULL DEFAULT 5,
  dedupe_key     text,
  worker_id      text,
  started_at     timestamptz,
  last_error     text,
  created_at     timestamptz NOT NULL DEFAULT now(),
  finished_at    timestamptz
);
-- sweeper：找尚未投遞或投遞後遺失的工作
CREATE INDEX ix_jobs_undispatched ON background_jobs(run_at) WHERE status IN ('queued','retrying');
CREATE UNIQUE INDEX uq_jobs_dedupe ON background_jobs(dedupe_key)
  WHERE dedupe_key IS NOT NULL AND status IN ('queued','running','retrying');
CREATE INDEX ix_jobs_stuck ON background_jobs(started_at) WHERE status = 'running';

CREATE TABLE llm_calls (
  id                   bigint GENERATED ALWAYS AS IDENTITY,
  created_at           timestamptz NOT NULL DEFAULT now(),
  user_id              uuid,
  session_id           uuid,
  purpose              text NOT NULL,        -- resume_parse, jd_parse, prep, question_gen, followup, evaluate, report, stt_quick, stt, tts, realtime, embedding
  provider             text NOT NULL DEFAULT 'openai',
  model                text NOT NULL,
  prompt_version       text,
  input_tokens         int,
  output_tokens        int,
  cached_tokens        int,
  audio_seconds        numeric(8,2),
  cost_usd             numeric(10,6),
  latency_ms           int,
  status               text NOT NULL CHECK (status IN ('ok','error','timeout')),
  provider_request_id  text,
  error_code           text,
  error_message        text,
  PRIMARY KEY (id, created_at)
) PARTITION BY RANGE (created_at);
CREATE INDEX ix_llm_calls_session ON llm_calls(session_id);
CREATE INDEX ix_llm_calls_user    ON llm_calls(user_id, created_at);

CREATE TABLE voice_events (
  id                   bigint GENERATED ALWAYS AS IDENTITY,
  created_at           timestamptz NOT NULL DEFAULT now(),
  session_id           uuid NOT NULL,
  session_question_id  uuid,
  source               text NOT NULL CHECK (source IN ('client','server')),  -- client：前端隨 heartbeat 回報
  event_type           text NOT NULL,        -- playback_finished, autoplay_blocked, realtime_connected, realtime_degraded, phase_change…
  payload              jsonb,
  PRIMARY KEY (id, created_at)
) PARTITION BY RANGE (created_at);
CREATE INDEX ix_voice_events_session ON voice_events(session_id, created_at);

CREATE TABLE app_events (
  id          bigint GENERATED ALWAYS AS IDENTITY,
  created_at  timestamptz NOT NULL DEFAULT now(),
  user_id     uuid,
  event_name  text NOT NULL,                 -- interview.started, interview.completed, resume.deleted, auth.login…
  properties  jsonb NOT NULL DEFAULT '{}',
  request_id  text,
  ip          inet,
  user_agent  text,
  PRIMARY KEY (id, created_at)
) PARTITION BY RANGE (created_at);
CREATE INDEX ix_app_events_name ON app_events(event_name, created_at);
CREATE INDEX ix_app_events_user ON app_events(user_id, created_at);


-- updated_at 觸發器
CREATE TRIGGER trg_users_updated BEFORE UPDATE ON users FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_resumes_updated BEFORE UPDATE ON resumes FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_job_posts_updated BEFORE UPDATE ON job_posts FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_target_jobs_updated BEFORE UPDATE ON target_jobs FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_interview_preps_updated BEFORE UPDATE ON interview_preps FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_prep_checklist_items_updated BEFORE UPDATE ON prep_checklist_items FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_question_sets_updated BEFORE UPDATE ON question_sets FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_question_set_items_updated BEFORE UPDATE ON question_set_items FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_interview_sessions_updated BEFORE UPDATE ON interview_sessions FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_answer_attempts_updated BEFORE UPDATE ON answer_attempts FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- 職能分類初始資料
INSERT INTO job_categories (code, label, sort_order) VALUES
  ('frontend','前端工程',1),('backend','後端工程',2),('fullstack','全端工程',3),('mobile','行動開發',4),
  ('data','資料工程與分析',5),('ml','機器學習',6),('devops','DevOps／SRE',7),('qa','測試與品質',8),
  ('pm','產品經理',9),('design','設計',10),('other','其他',99);
