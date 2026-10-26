-- 0004 個人檔案加入作品集連結，以及這份分析要用哪一份履歷（空＝主要履歷）。
ALTER TABLE career_profiles ADD COLUMN portfolio_links text[] NOT NULL DEFAULT '{}';
ALTER TABLE career_profiles ADD COLUMN resume_id uuid REFERENCES resumes(id) ON DELETE SET NULL;
