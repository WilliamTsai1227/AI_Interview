-- 0011 會員方案與 token 額度
--   plans              ：方案定義（每月 token／面試上限），改額度只要改資料，不用改程式
--   users              ：plan 改成參照 plans；可對單一使用者覆寫 token 上限、設定方案到期日
--   user_token_usage   ：每人每月 token 累計（每次 LLM 呼叫後累加），擋用量時查這張，不必加總 llm_calls
--   user_plan_changes  ：方案異動紀錄（誰、何時、從哪個方案改到哪個）

CREATE TABLE plans (
  code                     text PRIMARY KEY,                      -- free / pro / unlimited …
  name                     text NOT NULL,                         -- 顯示名稱
  monthly_token_limit      bigint CHECK (monthly_token_limit IS NULL OR monthly_token_limit >= 0),      -- NULL = 不限
  monthly_interview_limit  int    CHECK (monthly_interview_limit IS NULL OR monthly_interview_limit >= 0), -- NULL = 不限
  -- 已經開始的工作（面試中的追問、評分、報告）可以超用到 上限 × 這個倍數，避免做到一半被切斷
  hard_limit_ratio         numeric(4,2) NOT NULL DEFAULT 1.20 CHECK (hard_limit_ratio >= 1),
  price_twd                int NOT NULL DEFAULT 0,
  is_active                boolean NOT NULL DEFAULT true,         -- false：不能再指派給新使用者，已在用的不受影響
  sort_order               int NOT NULL DEFAULT 0,
  created_at               timestamptz NOT NULL DEFAULT now(),
  updated_at               timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER trg_plans_updated BEFORE UPDATE ON plans FOR EACH ROW EXECUTE FUNCTION set_updated_at();

INSERT INTO plans (code, name, monthly_token_limit, monthly_interview_limit, price_twd, sort_order) VALUES
  ('free',      'Free',      300000,  10,   0,   10),
  ('pro',       'Plus',      2000000, 30,   299, 20),
  ('unlimited', 'Unlimited', NULL,    NULL, 0,   90);   -- 內部／測試帳號用

-- users.plan：原本是 CHECK (plan IN ('free','pro'))，改成參照 plans
ALTER TABLE users DROP CONSTRAINT IF EXISTS users_plan_check;
ALTER TABLE users ADD CONSTRAINT fk_users_plan FOREIGN KEY (plan) REFERENCES plans(code) ON UPDATE CASCADE;
ALTER TABLE users
  ADD COLUMN plan_expires_at       timestamptz,   -- NULL = 不會到期；過期後一律當 free 計算
  ADD COLUMN token_limit_override  bigint CHECK (token_limit_override IS NULL OR token_limit_override >= 0);
                                                  -- 非 NULL 時取代方案的每月 token 上限（客服補額度、特殊合約）

CREATE TABLE user_token_usage (
  user_id        uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  period_start   date NOT NULL,                   -- 每月 1 日
  input_tokens   bigint NOT NULL DEFAULT 0,
  output_tokens  bigint NOT NULL DEFAULT 0,
  cached_tokens  bigint NOT NULL DEFAULT 0,       -- 只做統計，不另外扣
  total_tokens   bigint GENERATED ALWAYS AS (input_tokens + output_tokens) STORED,
  call_count     int NOT NULL DEFAULT 0,
  updated_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, period_start)
);

CREATE TABLE user_plan_changes (
  id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id               uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  from_plan             text,
  to_plan               text NOT NULL,
  token_limit_override  bigint,
  plan_expires_at       timestamptz,
  changed_by            uuid REFERENCES users(id) ON DELETE SET NULL,   -- NULL = 系統
  reason                text,
  created_at            timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_user_plan_changes_user ON user_plan_changes(user_id, created_at DESC);

-- 本月已經用掉的 token 從 llm_calls 補進來，上線當下就有正確的用量
INSERT INTO user_token_usage (user_id, period_start, input_tokens, output_tokens, cached_tokens, call_count)
SELECT c.user_id, date_trunc('month', now())::date,
       COALESCE(sum(c.input_tokens), 0), COALESCE(sum(c.output_tokens), 0), COALESCE(sum(c.cached_tokens), 0), count(*)
  FROM llm_calls c JOIN users u ON u.id = c.user_id
 WHERE c.created_at >= date_trunc('month', now()) AND c.status = 'ok'
 GROUP BY c.user_id;
