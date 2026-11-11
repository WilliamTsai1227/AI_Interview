-- 0012 記錄登入方式：管理後台只開放「這次是用 Google 登入」的帳號
--   refresh_tokens.auth_method 跟著整條 refresh 鏈（family）傳下去，換發 access token 時寫進 JWT 的 amr
--   既有的 token 一律視為 password：上線後要用後台的人重新用 Google 登入一次即可
ALTER TABLE refresh_tokens
  ADD COLUMN auth_method text NOT NULL DEFAULT 'password' CHECK (auth_method IN ('password', 'google'));
