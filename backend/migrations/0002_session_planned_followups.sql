-- 0002 預設追問快照：開始面試時把主題目的預設追問複製到 session_questions，
-- 面試進行中修改面試題庫不影響這場面試（api.md §7「只影響之後開始的面試」）。
ALTER TABLE session_questions ADD COLUMN planned_followups jsonb;   -- 只在主題目、followup_mode = planned：[{"id","text"}]
COMMENT ON COLUMN session_questions.planned_followups IS '主題目的預設追問快照（followup_mode = planned）';
