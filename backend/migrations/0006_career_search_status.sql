-- 0006 職涯規劃的網路搜尋紀錄：搜尋幾次（失敗會自動重試一次）、結果、失敗原因、網址檢查
ALTER TABLE career_plans
  ADD COLUMN IF NOT EXISTS search_status   text CHECK (search_status IN ('running','ok','failed','skipped')),
  ADD COLUMN IF NOT EXISTS search_attempts smallint NOT NULL DEFAULT 0,      -- 第幾次搜尋（2＝重新搜尋過 1 次）
  ADD COLUMN IF NOT EXISTS search_error    text,
  ADD COLUMN IF NOT EXISTS web_found       int NOT NULL DEFAULT 0,          -- 搜尋回來幾筆
  ADD COLUMN IF NOT EXISTS web_dropped     int NOT NULL DEFAULT 0;          -- 網址無效被移除幾筆
ALTER TABLE career_plan_jobs
  ADD COLUMN IF NOT EXISTS url_check text CHECK (url_check IN ('ok','unverified'));   -- 網路職缺的網址檢查結果
