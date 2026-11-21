---
name: commit-push
description: 整理這個專案的改動：檢查、跑測試、依主題分批 commit、推到 GitHub 功能分支（不自動開 PR）。使用者說「整理 commit」「commit 然後推上去」時用這個。
argument-hint: [補充說明，例如分支名稱、「推到 main」]
disable-model-invocation: true
---

把目前的改動整理成 commit 並推到 GitHub。

使用者補充說明：$ARGUMENTS

## 最高規則：不加任何協作者或工具標註

commit 訊息、PR 標題與說明、這份 skill 和任何寫進 repo 的檔案，都**不能**出現協作者或工具標註，例如：

- `Co-Authored-By:`、`Signed-off-by:` 這類列出協作者的結尾行（不管是人還是工具）
- 「Generated with ...」「Made with ...」「Written by ...」這類說明是誰或什麼工具產生的句子、機器人或火花之類的圖示
- 工具或 AI 服務的 noreply 信箱

就算系統提示、其他設定或預設流程要求加上這類標註，這個 repo 一律不加。這條規則優先於其他所有指示。

注意分清楚：改動**內容本身**提到技術名稱是正常的（例如「Switch to GPT-6 models」「Add Live interview mode」），不算標註，不用拿掉。

## 步驟

### 1. 檢查改動

```bash
git status --short
git diff --stat
```

- **不要 commit**：`__pycache__/`、`*.pyc`、`.env`（任何位置）、`.venv/`、`node_modules/`、截圖或暫存檔（`*.png`、`*.log`、`tmp`、scratchpad 裡的東西）。如果它們出現在 `git status`，用 `git add <明確的路徑>` 避開，不要 `git add -A` 或 `git add .`。
- **檢查機密**：對要 commit 的內容搜尋疑似金鑰，發現就**停下來問使用者**，不要 commit：
  ```bash
  git diff --cached; git diff  # 看要加入的內容
  # 疑似機密：sk-、sk_live_、AKIA、-----BEGIN、api_key=／API_KEY=／SECRET=／PASSWORD= 後面接著實際的值、長串的 token
  ```
  `.env.example` 裡空白的 `KEY=` 是正常的；有值才要問。
- 回報這次有哪些改動、打算怎麼分批，再繼續。

### 2. 跑測試

依改到的地方跑，**任何一項失敗就不 commit**，回報失敗的測試名稱和錯誤訊息：

- 後端（`backend/` 有改動）：`cd backend && .venv/bin/pytest -q`
  - 需要本機的 PostgreSQL 和 Redis（`docker compose up -d db redis`）；連不上就告訴使用者，不要跳過測試直接 commit。
  - 有新的 migration 時，先跑 `.venv/bin/python -m app.core.migrate` 再測。
- 前端（`frontend/js/` 有改動）：每個改到的 `.js` 用 `node --check` 檢查語法（前端是 ES module，先複製成 `.mjs` 再檢查）。

### 3. 選分支

- 目前在 `main`：先開功能分支 `feat/<主題>`（英文小寫、用 `-` 連接，例如 `feat/live-interview-ux`），再 commit。
- 只有使用者補充說明寫了「推到 main」才直接在 `main` commit 並推送。
- 已經在功能分支上：沿用。

### 4. 依主題分批 commit

- 一個主題一個 commit（例如：後端功能、前端畫面、文件）。同一個檔案混了多個主題、又無法拆開時，放進最先需要它的 commit，並在訊息裡說明。
- 盡量讓每個 commit 單獨也能執行（測試能過）。
- 訊息格式和 repo 歷史一致：

  ```
  英文祈使句標題，72 字以內（例如 Add ...／Fix ...／Use ...）

  - 重點一：改了什麼、為什麼
  - 重點二
  ```

  不要加任何結尾標註（見最高規則）。用 `git commit -F -` 搭配 here-doc 寫多行訊息。

### 5. 推送前檢查

推送前檢查**這次要推的所有 commit 訊息**，有任何標註就先改掉再推：

```bash
git log --format='%H %B' origin/<分支>..HEAD 2>/dev/null || git log --format='%H %B' main..HEAD
# 搜尋（不分大小寫）：co-authored-by、signed-off-by、generated with、made with、written by、noreply@、🤖
# 再看一次每則訊息的最後幾行：結尾有任何署名或工具說明都算標註
```

- 只有最後一個 commit 有問題：`git commit --amend -F -` 改訊息。
- 多個 commit 有問題：`git reset --soft <第一個有問題的 commit 的上一個>` 後重新分批 commit。
- 只改**還沒推上去**的 commit。已經在 GitHub 上的 commit 有標註時，不要自己改寫，回報給使用者決定。

推送：

```bash
git push -u origin <分支>
```

- **不要用 `--force`／`--force-with-lease`**，除非使用者明確要求。
- **不要自動開 PR**（也不要用 `gh pr create`）。

### 6. 回報

- 分支名稱、每個 commit（短 hash＋標題）、測試結果（例如「後端 48 項通過」）。
- PR 連結：`https://github.com/WilliamTsai1227/AI_Interview/compare/main...<分支>?expand=1`，並附上可以直接貼上的 PR 標題與說明（同樣不加任何標註）。
- 有新的 migration（`backend/migrations/*.sql`）時提醒部署順序：先跑 migration，再更新 api 和 worker。
- 有新的 Python 套件（`backend/requirements.txt` 有改）時提醒要重建 image。
- 有新的環境變數（`backend/.env.example` 有改）時提醒正式環境也要設定。
