-- 0020 職缺解讀：Ava 用白話說明這個職缺實際在做什麼、想找什麼樣的人、要拿哪些經驗證明（職缺總覽頁）
--   跟職缺走、不跟使用者走：同一個平台職缺所有人共用一份；使用者貼的職缺依原文重新出題時重新產生
--   另開一張表，不寫進 job_posts：寫入 job_posts 會更新 updated_at，讓面試建議誤判「職缺有改，要重新分析」
-- 新表，舊版程式不會讀寫，滾動更新期間不影響舊版 Pod
CREATE TABLE IF NOT EXISTS job_insights (
  job_post_id     uuid PRIMARY KEY REFERENCES job_posts(id) ON DELETE CASCADE,
  status          text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','running','ready','failed')),
  insight         jsonb,             -- {essence, persona[], focus[{title, quote, meaning, evidence[], story}], experiences[{text, kind, proof}], signals[]}
  prompt_version  text,
  error           text,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
);
DROP TRIGGER IF EXISTS trg_job_insights_updated ON job_insights;
CREATE TRIGGER trg_job_insights_updated BEFORE UPDATE ON job_insights FOR EACH ROW EXECUTE FUNCTION set_updated_at();
