# 帳號與登入

## 為什麼做

練習紀錄、題庫、報告都是個人的，需要帳號保存。Google 登入讓使用者不用再記一組密碼，降低開始使用的門檻。

## 怎麼用

- Email＋密碼註冊（密碼至少 8 碼）。
- 或「使用 Google 繼續」。用同一個 Email 註冊過的帳號，Google 登入後會自動連結到原帳號。
- 登入會保持 30 天。

## 怎麼測試

**功能**
- 註冊、登入、登出、重新整理後仍在登入狀態。
- 密碼錯 5 次 → 這個 Email 鎖 15 分鐘。
- Google 登入成功；Google 帳號的 Email 未驗證 → 拒絕。
- 用 Google 建立的帳號改用密碼登入 → 提示改用 Google。

**安全**
- 登入相關的 Cookie 是 httpOnly、SameSite=Strict；正式環境要開 `COOKIE_SECURE=true`。
- 同一個 IP 短時間大量登入 → 被限制次數。
- 正式環境不要建立示範帳號（`SEED_DEMO` 保持 `false`）。
