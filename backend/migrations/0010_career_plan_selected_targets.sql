-- 0010 求職準備：使用者自己選這次要比對哪些目標職缺（1–5 個），不再自動抓全部。
-- target_job_ids 存 job_posts.id（目標職缺之後被移除，這份分析仍知道當時比對了哪些職缺）；舊分析是空陣列。
ALTER TABLE career_plans ADD COLUMN IF NOT EXISTS target_job_ids uuid[] NOT NULL DEFAULT '{}';
