-- 0018 履歷修改讀取 GitHub 作品（spec/features/resume-revision.md「GitHub 作品」）
--   使用者在設定時勾選 repo（setup.portfolio_links），產生卡片前先實際讀原始碼並審閱（沿用能力落差分析的 career/github.py）
--   portfolio：每個連結的讀取結果與審閱內容；stage：carding 時正在做哪一步（portfolio 讀 GitHub → cards 產生卡片）
-- 只加可為 NULL 的欄位，舊版程式不會讀寫，滾動更新期間不影響舊版 Pod
ALTER TABLE resume_revisions ADD COLUMN portfolio jsonb;
ALTER TABLE resume_revisions ADD COLUMN stage text;
