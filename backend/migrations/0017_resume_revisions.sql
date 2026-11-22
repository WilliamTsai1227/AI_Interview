-- 0017 履歷修改（spec/features/resume-revision.md）
--   設定（目的、履歷、職缺、輸出偏好）→ Ava 依履歷＋職缺產生挖掘卡片 → 使用者逐張回答 → 產生新版履歷（逐條標出改動）
--   → 使用者逐條接受／還原／編輯 → 下載 Word、複製文字、存成新的一份履歷
-- 新增資料表，舊版程式不會讀寫，滾動更新期間不影響舊版 Pod
CREATE TABLE resume_revisions (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id         uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  resume_id       uuid REFERENCES resumes(id) ON DELETE SET NULL,
  target_job_ids  uuid[] NOT NULL DEFAULT '{}',   -- job_posts.id，依使用者選擇順序；空＝整體改善，不依職缺
  setup           jsonb NOT NULL,                 -- {goal, language, length, correction}
  status          text NOT NULL DEFAULT 'carding'
                  CHECK (status IN ('carding','answering','generating','ready','failed')),
  cards           jsonb,                          -- [{kind, context, question, options:[{label, opens_detail}], detail_prompt, example}]
  answers         jsonb NOT NULL DEFAULT '{}',    -- {"卡片序號": {choice, text, skipped}}
  result          jsonb,                          -- {name, headline, contact, sections:[{title, entries:[{title, meta, lines:[{id, text, change, original, reason, card}]}]}], summary, tips}
  decisions       jsonb NOT NULL DEFAULT '{}',    -- {line_id: {accept, text}}：使用者還原或改過的才有
  saved_resume_id uuid REFERENCES resumes(id) ON DELETE SET NULL,
  error           text,
  prompt_version  text,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  finished_at     timestamptz
);
CREATE INDEX ix_resume_revisions_user ON resume_revisions(user_id, created_at DESC);
CREATE TRIGGER trg_resume_revisions_updated BEFORE UPDATE ON resume_revisions FOR EACH ROW EXECUTE FUNCTION set_updated_at();
