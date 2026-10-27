-- 0005 職缺爬蟲執行明細：每家公司抓到幾筆、新增／更新幾筆、遇到的問題（robots.txt 不允許、機器人驗證、沒有結構化資料…）
-- 可重複執行：曾以 0003_ingest_run_details.sql 套用過的資料庫，再跑一次不會出錯
ALTER TABLE job_ingest_runs ADD COLUMN IF NOT EXISTS details jsonb NOT NULL DEFAULT '{}';
COMMENT ON COLUMN job_ingest_runs.details IS '執行明細：{"mode","companies":{name:{jobs,inserted,updated,issues[]}},"summarized",…}';
