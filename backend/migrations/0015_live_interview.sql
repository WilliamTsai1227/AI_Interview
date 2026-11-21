-- 0015 Live 即時對談（spec/features/mock-interview.md「Live 模式」、architecture.md §6.10）
--   voice_mode 多一個 live：GPT-Live 全雙工對談，面試中不逐題錄音；結束後才把逐字稿切成題目評分
--   live_brief：建立場次時產生的「面試官簡報」（職缺必考能力、履歷亮點與疑點、面試議程）
--   session_turns：live 模式的對話逐字稿（前端把 GPT-Live 的雙方字幕整理成一段一段送上來）
--   followup_source 多一個 live：從逐字稿切出來的追問
-- 只是放寬 CHECK（舊版程式不會寫入新值），滾動更新期間不影響舊版 Pod
ALTER TABLE interview_sessions DROP CONSTRAINT IF EXISTS interview_sessions_voice_mode_check;
ALTER TABLE interview_sessions ADD CONSTRAINT interview_sessions_voice_mode_check
  CHECK (voice_mode IN ('scripted','realtime','live'));
ALTER TABLE interview_sessions ADD COLUMN live_brief jsonb;   -- 只在 live：{seeds, must_haves, highlights, concerns, topics}

ALTER TABLE session_questions DROP CONSTRAINT IF EXISTS session_questions_followup_source_check;
ALTER TABLE session_questions ADD CONSTRAINT session_questions_followup_source_check
  CHECK (followup_source IN ('fixed_ai','fixed_fallback','planned','adaptive_ai','live'));

CREATE TABLE session_turns (
  session_id  uuid NOT NULL REFERENCES interview_sessions(id) ON DELETE CASCADE,
  seq         int  NOT NULL,                 -- 前端編號，重送同一段不會重複寫入
  role        text NOT NULL CHECK (role IN ('interviewer','candidate')),
  text        text NOT NULL,
  started_ms  int,                           -- 這段話在該次連線中的開始／結束時間（暫停後重新連線會從 0 算）
  ended_ms    int,
  created_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (session_id, seq)
);
