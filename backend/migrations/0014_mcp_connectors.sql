-- 0014 MCP 連接器（spec/features/mcp-connectors.md）：使用者授權自己的 Notion，token 加密存放
--   mcp_oauth_clients：向 MCP 授權伺服器做 Dynamic Client Registration 的結果，全站共用一份
--   mcp_connections：每個使用者每個 provider 一筆，token 用 AES-GCM 加密（app/core/crypto.py）
CREATE TABLE mcp_oauth_clients (
  provider      text PRIMARY KEY,
  client_id     text NOT NULL,
  client_secret bytea,                       -- 加密；public client 時為 NULL
  metadata      jsonb NOT NULL DEFAULT '{}', -- 授權 / token / 撤銷端點、註冊時的 redirect_uri
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE mcp_connections (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id       uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  provider      text NOT NULL CHECK (provider IN ('notion')),
  account_label text,                        -- 顯示用：Notion workspace 名稱
  access_token  bytea NOT NULL,
  refresh_token bytea,
  key_version   smallint NOT NULL DEFAULT 1,
  expires_at    timestamptz,
  status        text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'error')),
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (user_id, provider)
);
