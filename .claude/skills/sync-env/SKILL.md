---
name: sync-env
description: 把 backend/.env 對齊 backend/.env.example（補上新設定、照範本排序，原本的值都保留）。只要改過 .env.example（新增或調整環境變數），就要主動執行，不用等使用者開口；使用者說「同步 .env」「對標 .env.example」時也用這個。
---

把本機的 `backend/.env` 對齊 `backend/.env.example`。

## 什麼時候做

- **改過 `backend/.env.example` 之後一定要做**（新增、改名、調整預設值），在同一輪工作裡做完，不用問使用者。
- 使用者要求同步 `.env` 時。

## 步驟

1. 執行：
   ```bash
   python3 .claude/skills/sync-env/sync_env.py
   ```
   腳本會：
   - 照 `.env.example` 的順序與註解重寫 `.env`
   - `.env` 原本有的值全部保留（包含範本裡被註解、但 `.env` 有設定的）
   - 缺的設定用範本的預設值補上
   - `.env` 有、範本沒有的設定放到最後
   - 寫完檢查原本的值都還在，有遺失就回報錯誤（結束碼 2）
   只想看差異、不寫入：加 `--check`。
2. 跟使用者回報補了哪些設定名稱。
   - 補上的設定如果是金鑰（例如 `*_API_KEY`、`*_SECRET`、`*_PASSWORD`）而且是空白，提醒使用者要自己填，並說明去哪裡取得（看 `.env.example` 的註解）。
   - 設定改完要重啟 api／worker 才會生效。

## 注意

- `.env` 裡有金鑰：**不要印出、讀出或貼出任何設定值**，只講設定名稱。不要用 `cat`、`Read` 看 `.env` 內容。
- 不要把 `.env` 加進 git（已在 `.gitignore`）。
- 腳本只處理 `KEY=value` 一行一個的格式；遇到多行值或特殊格式時，停下來告訴使用者，不要硬改。
