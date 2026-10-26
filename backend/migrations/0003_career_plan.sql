-- 0003 職涯差距分析：個人檔案（自我介紹、求職意向）→ 找職缺 → 能力差距 → 準備計畫（大項目／小項目）。
-- 每位使用者一份檔案；每次「開始分析」產生一份 career_plans，保留歷史。
CREATE TABLE career_profiles (
  user_id          uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  self_intro       text NOT NULL DEFAULT '',          -- 自我介紹
  experience_note  text NOT NULL DEFAULT '',          -- 履歷以外的經歷補充
  salary_min       int CHECK (salary_min >= 0),       -- 期望最低薪資
  salary_period    text NOT NULL DEFAULT 'month' CHECK (salary_period IN ('month','year')),
  industries       text[] NOT NULL DEFAULT '{}',
  roles            text[] NOT NULL DEFAULT '{}',      -- 想做的職務
  duties_pref      text NOT NULL DEFAULT '',          -- 希望的工作內容
  work_hours       text NOT NULL DEFAULT '',          -- 工時偏好，例如「彈性上下班、不加班」
  locations        text[] NOT NULL DEFAULT '{}',
  work_modes       text[] NOT NULL DEFAULT '{}' CHECK (work_modes <@ ARRAY['onsite','hybrid','remote']),
  updated_at       timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE career_plans (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id          uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  resume_id        uuid REFERENCES resumes(id) ON DELETE SET NULL,
  status           text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','running','ready','failed')),
  profile_snapshot jsonb NOT NULL DEFAULT '{}',       -- 分析當下的個人檔案
  summary          text,
  readiness_score  int CHECK (readiness_score BETWEEN 0 AND 100),
  strengths        text[] NOT NULL DEFAULT '{}',
  gaps             jsonb NOT NULL DEFAULT '[]',       -- [{"skill","severity":"high|medium|low","detail"}]
  web_search_used  boolean NOT NULL DEFAULT false,
  error            text,
  prompt_version   text,
  created_at       timestamptz NOT NULL DEFAULT now(),
  finished_at      timestamptz
);
CREATE INDEX ix_career_plans_user ON career_plans(user_id, created_at DESC);

CREATE TABLE career_plan_jobs (
  plan_id      uuid NOT NULL REFERENCES career_plans(id) ON DELETE CASCADE,
  job_post_id  uuid NOT NULL REFERENCES job_posts(id) ON DELETE CASCADE,
  found_by     text NOT NULL CHECK (found_by IN ('catalog','web')),
  match_score  int,
  reason       text,
  gaps         text[] NOT NULL DEFAULT '{}',
  PRIMARY KEY (plan_id, job_post_id)
);

CREATE TABLE career_plan_areas (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  plan_id     uuid NOT NULL REFERENCES career_plans(id) ON DELETE CASCADE,
  title       text NOT NULL,
  why         text NOT NULL DEFAULT '',
  priority    text NOT NULL DEFAULT 'medium' CHECK (priority IN ('high','medium','low')),
  sort_order  int NOT NULL
);
CREATE INDEX ix_career_areas_plan ON career_plan_areas(plan_id, sort_order);

CREATE TABLE career_plan_tasks (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  area_id     uuid NOT NULL REFERENCES career_plan_areas(id) ON DELETE CASCADE,
  plan_id     uuid NOT NULL REFERENCES career_plans(id) ON DELETE CASCADE,
  text        text NOT NULL,
  is_checked  boolean NOT NULL DEFAULT false,
  sort_order  int NOT NULL
);
CREATE INDEX ix_career_tasks_area ON career_plan_tasks(area_id, sort_order);
