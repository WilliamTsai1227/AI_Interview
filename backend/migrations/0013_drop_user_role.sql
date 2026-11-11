-- 0013 拿掉 users.role：管理權限改由管理後台三關決定（ADMIN_GOOGLE_EMAILS＋Google 登入＋後台帳密，見 admin/console.py）
--   舊的 access token 裡還有 role 欄位，程式已不讀取，過期後自然消失
ALTER TABLE users DROP COLUMN IF EXISTS role;
