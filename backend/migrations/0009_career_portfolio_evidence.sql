-- 0009 職涯規劃實際讀取 GitHub 作品集：每個連結讀到什麼、Ava 的審閱結果，畫面上可以展開檢查。
-- portfolio_evidence：[{"url","status":"ok|failed|skipped","error","kind":"repo|profile","repo":{...},"files_read":[...],"analysis":{...}}]
-- stage：分析進行到哪一步（portfolio＝讀作品集、plan＝排準備計畫），給前端顯示進度
ALTER TABLE career_plans
  ADD COLUMN IF NOT EXISTS portfolio_evidence jsonb NOT NULL DEFAULT '[]',
  ADD COLUMN IF NOT EXISTS stage text CHECK (stage IN ('portfolio','plan'));
