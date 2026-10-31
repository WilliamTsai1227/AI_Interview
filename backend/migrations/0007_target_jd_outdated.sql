-- 0007 目標職缺：使用者編輯了貼上的原始職缺內容，但還沒按「依新內容重新出題」。
-- 編輯原文不會自動觸發 AI；這個旗標讓畫面提示題庫與面試建議仍是依舊內容產生的。
ALTER TABLE target_jobs ADD COLUMN IF NOT EXISTS jd_outdated boolean NOT NULL DEFAULT false;
