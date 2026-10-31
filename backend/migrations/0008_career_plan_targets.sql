-- 0008 職涯規劃改成比對使用者自己的目標職缺（不再用平台職缺與網路搜尋）。
-- career_plan_jobs.found_by 新增 'target'；舊分析的 'catalog'／'web' 紀錄保留。
-- career_plans 的 search_* 與 career_plan_jobs.url_check 欄位不再寫入，舊資料保留。
ALTER TABLE career_plan_jobs DROP CONSTRAINT IF EXISTS career_plan_jobs_found_by_check;
ALTER TABLE career_plan_jobs ADD CONSTRAINT career_plan_jobs_found_by_check CHECK (found_by IN ('target','catalog','web'));
