---
name: notion-dev
description: 把這次的開發工作（功能、變更、重構、部署）整理後記錄到 Notion「系統開發」底下對應的 inline 資料庫
argument-hint: [這次開發的內容或補充說明]
disable-model-invocation: true
---

把這次的開發工作記錄到 Notion「系統開發」。

使用者補充說明：$ARGUMENTS

## 先讀
- `.claude/notion-workspace.md`：位置、資料庫快取、共用規則（一定要照著做）。
- 系統開發頁面：https://app.notion.com/p/3f62dbee508080f3a37bc4aaee31aed2 ，底下有 3 個 inline 資料庫。

## 收集內容
- `git branch --show-current`、`git log --oneline main..HEAD`（沒有分支差異時用 `git log --oneline -10`）、`git diff main...HEAD --stat`。
- 有 PR 時用 `gh pr view --json number,title,url` 拿連結。
- 對話裡做過的決定和原因。

## 步驟
1. fetch 系統開發頁面與 3 個資料庫，對照快取表，判斷這次工作該寫到哪個（或哪幾個）資料庫。分不出來先問使用者。
2. 同一個分支／功能已經有列 → 更新；沒有才新增。
3. 有狀態、日期、分支、PR 這類欄位就填（狀態依實際情況：開發中／已合併…，照欄位既有選項）。
4. 內文：
   - **目標**：為什麼做
   - **改了什麼**：依模組列出，附主要檔案路徑
   - **設計決定與取捨**
   - **commit／PR**
   - **怎麼驗證**：跑了哪些測試、結果（沒跑就寫「未測試」）
   - **後續待辦**
5. 規格有變（API、資料表、架構）時，提醒使用者再跑 `/notion-design`；有測試結果時提醒 `/notion-test`。
6. 回報寫入位置與連結。
