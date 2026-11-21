-- 0016 面試中的即時評分（spec/features/mock-interview.md「面試畫面」、architecture.md §6.10）
--   依題目模式：每題錄音後本來就會評分（evaluations），右側欄直接讀，不需要新表
--   Live 模式：每段「Ava 問 → 你答 → Ava 接下一句」完成，就用快速模型評一次寫在這裡
--   這只是面試中的參考；正式分數仍是結束後切題 → evaluate_attempt 的報告
CREATE TABLE live_feedback (
  session_id   uuid NOT NULL REFERENCES interview_sessions(id) ON DELETE CASCADE,
  seq          int  NOT NULL,                -- 這段回答第一句的 seq
  answer_seqs  int[] NOT NULL,
  question     text NOT NULL,                -- Ava 這段說的話（原文）
  answer       text NOT NULL,
  status       text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','done','skipped','failed')),
  title        text,                         -- 這題在問什麼（一句話）
  score        smallint CHECK (score BETWEEN 0 AND 100),
  strengths    jsonb,
  improvements jsonb,
  tip          text,                         -- 下一題可以馬上用上的提醒
  created_at   timestamptz NOT NULL DEFAULT now(),
  finished_at  timestamptz,
  PRIMARY KEY (session_id, seq)
);
