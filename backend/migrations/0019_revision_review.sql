-- 0019 履歷修改的初篩檢查（spec/features/resume-revision.md「初篩檢查」）
--   新版履歷產生後，用 HR／招募與用人主管的角度再審一次：初篩判斷、新舊分數、關鍵字覆蓋、還要改的地方
--   review：結果（含審閱當時的履歷雜湊，使用者改過之後可以看出要重新檢查）；review_status：pending／running／ready／failed
-- 只加可為 NULL 的欄位，舊版程式不會讀寫，滾動更新期間不影響舊版 Pod
ALTER TABLE resume_revisions ADD COLUMN review jsonb;
ALTER TABLE resume_revisions ADD COLUMN review_status text CHECK (review_status IN ('pending','running','ready','failed'));
