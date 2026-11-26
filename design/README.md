# AI Interview 設計稿與設計規範

> **最新方向：保留原始 UIUX，只轉換風格。** 請先看 [forma-preserved/README.md](forma-preserved/README.md) 與 [實際預覽截圖](forma-preserved/preview-editorial.png)。此版直接使用原本 frontend 頁面，加上可切換的簡潔樣式；原始側欄、職缺分頁、表單與操作順序保留。下方 forma. 頂部導覽版及 v1–v3B 為先前方案，保留比較。

> **目前新增方向：forma. / Editorial Minimalism**。新版為獨立互動原型，入口 [forma/index.html](forma/index.html)，執行與設計規範見 [forma/README.md](forma/README.md)。保留 AI 面試產品定位，改用米白、朱紅、頂部導覽、巨型標題與直角細線結構。下方 v1–v3B 與 `.fig` 皆為歷史方案，保留比較；正式前端尚未修改。

> 狀態：**設計稿，尚未實作**。正式前端 `frontend/`、後端與資料庫都沒有修改。
> 可編輯原檔：[ai-career-coach.fig](ai-career-coach.fig)（用 OpenPencil 開啟）

## 換電腦接續工作

1. 安裝 OpenPencil 桌面 App：<https://github.com/open-pencil/open-pencil/releases>（Apple 晶片下載 `aarch64.dmg`，Intel 下載 `x64.dmg`）。
2. 安裝 MCP 並加進 Claude Code：
   ```bash
   npm install -g @open-pencil/mcp
   claude mcp add --scope user open-pencil -- openpencil-mcp
   claude mcp add --scope user playwright -- npx -y @playwright/mcp@latest   # 選用：實際操作網站
   ```
3. 打開 App → 開啟 `design/ai-career-coach.fig` → 重開 Claude Code，`claude mcp list` 確認連線。
4. 跟 Claude 說：「讀 design/README.md，從『下一步』繼續」。

## 文件裡的頁面

| 頁面 | 內容 | 狀態 |
|---|---|---|
| `Dashboard v3 — A 藍紫 / B 淺橘` | 左側欄＋兩種配色並排 | **選定 B（右邊那張 `Dashboard v3B`）** |
| `Dashboard v2` | 上方導覽、深色橘紅 | 已放棄（改回側欄、橘色太深） |
| `Dashboard` / `Design System` | v1，Linear 風格淺色 | 已放棄，保留對照 |

## v3B（選定方向）

預覽：[preview-dashboard-v3b-peach.png](preview-dashboard-v3b-peach.png)（A 版對照：[preview-dashboard-v3a-violet.png](preview-dashboard-v3a-violet.png)）

**版面**
- 左側欄 256：品牌 → 搜尋 → 「面試暫停中・繼續」深色小卡 → 總覽／新面試 → 職缺（公司字首方塊＋準備度 %）→ 面試報告／履歷修改／能力落差分析 → 底部使用者。沿用現有網站結構。
- 主區只講兩件事：Hero（準備度＋今天該做的一件事＋Ava 對話卡）、準備路線（時間軸＋三階段卡）。其他數據留給報告頁。

**色彩（不用深酒紅、不用紅棕深色）**

| 用途 | 色碼 |
|---|---|
| 頁面底 | `#F7F4F0` |
| 淺卡片 | `#F0EAE3`，白卡 `#FFFFFF`，邊框 `#EAE3DB` |
| Hero 底 | `#FFF4EC`，光暈 `#FF9A62`（75%）、`#FFD3A1`、`#FFC2AE`（70%），layer blur 160–180 |
| 深色（主按鈕、進行中卡、暫停卡） | 中性炭黑 `#1C1B1A` |
| 文字 | `#1C1B1A`／`#5E554C`／`#8E867D`／`#BFB6AC` |
| 強調 | 橘 `#FF8A50`（頭像、進度起點）、`#E5602F`（淺底上的強調文字）、杏 `#FFD3A1`（深底上的強調） |
| 進度條 | 漸層 `#FF8A50 → #FFD3A1` |

**字型**：中文 Noto Sans TC（大標 600、字距 -1.8）；數字與英文 Inter／Inter Tight。
**形狀**：Hero 圓角 28、階段卡 26、內層列 18、按鈕與標籤全圓。主按鈕是「黑色時間段＋白色膠囊」的分段按鈕。

### 從現有網站保留的元素（2026-11-26 用 Playwright 實際操作確認）

- 側欄「面試暫停中・第 x/5 題・繼續」橫幅
- 職缺以公司字首做小方塊
- 面試間：深色舞台、Ava 頭像＋字幕、分段進度條、麥克風大按鈕、右側題目＋即時評分
- 題庫：「想加強什麼？」Ava 輸入框＋題目類型／難度／題數
- 黑色膠囊主按鈕、「你的面試官 Ava」

## 下一步

1. 依 v3B 重建 `Design System` 頁（色彩、字級、元件），並把顏色建成 OpenPencil 變數。
2. 畫面試間（沿用現有深色舞台，套 v3B 配色）。
3. 面試報告 → 職缺頁（總覽／題庫／面試建議／報告）。
4. 全部確認後才開始改 `frontend/`。

---

## v2（已放棄）

預覽：[preview-dashboard-v2.png](preview-dashboard-v2.png)　原檔頁面：`Dashboard v2`

- **導覽改到上方**：浮動膠囊導覽（總覽／練習／報告／成長），中間是職缺切換器，取代側欄的職缺清單。
- **Dashboard 只講兩件事**：Hero 告訴你準備度與今天該做的一件事；「準備路線」三階段卡片告訴你離一面還有多遠。其他數據移到報告頁。
- **視覺**：深色底＋餘燼橘／琥珀光暈（layer blur）、大字雙色標題（第二行降低不透明度）、膠囊按鈕、玻璃感 Ava 卡片、淺卡片中夾一張深色「進行中」卡。
- **色彩**：ink `#120E0C`、paper `#F4F2EE`、stone `#ECE8E2`、ember `#E8562A`、amber `#F2A65A`、wine `#7A1E12`、muted text `#6B655E` / `#8A847D` / `#B3ADA5`。
- **字型**：中文 Noto Sans TC 600（大標字距 -2）；數字與英文 Inter Tight。
- **圓角**：外框 28、內層 18–22、按鈕與標籤 full。

v2 方向確認後，`Design System` 頁會依 v2 重建；以下 v1 內容保留作對照。

---

## v1（已被 v2 取代）

> 狀態：**設計稿，尚未實作**。正式前端 `frontend/` 沒有任何修改。
> 可編輯原檔：[ai-career-coach.fig](ai-career-coach.fig)（用 OpenPencil 開啟，含 `Dashboard`、`Design System` 兩頁與變數）

| 預覽 | 檔案 |
|---|---|
| Dashboard（1440 桌面版） | [preview-dashboard.png](preview-dashboard.png) |
| Design System | [preview-design-system.png](preview-design-system.png) |

## 1. 設計方向：AI Career Coach

把「針對一個職缺反覆練習」升級成「每個職缺都有**準備度**」，登入後先看到 Dashboard，而不是直接進新面試頁。

- **一個下一步**：Ava 只推薦一件最值得做的事（重練弱項、繼續暫停的面試、補履歷），附預估時間與理由。
- **準備度看得到**：每個職缺有分數與狀態（剛開始／加強中／快準備好了），側欄職缺前的圓點顏色同步。
- **AI 透明**：背景工作（出題、評分、解析）集中在「Ava 正在處理」，不再散在各頁。
- **閉環**：報告 → 待加強題目 → 一鍵加入題組 → 再練一次 → 分數趨勢。

風格：冷靜、精準、資訊密度高。中性暖灰當底，靛藍主色只用在「下一步」和 AI，語意色只用來表達分數與狀態。

## 2. 新的資訊架構（提案）

```
側欄
├ 總覽（Dashboard，新）            #dashboard  ← 登入後預設
├ 開始練習                         #new
├ 面試報告                         #report
├ 我的職缺（依最近練習排序，圓點 = 準備度）  #targets/{id}
└ 成長
   ├ 履歷修改                      #revise
   └ 能力落差分析                  #career
底部：本月練習額度、帳號與設定
```

## 3. Dashboard 區塊與資料來源

全部沿用現有 API，不需要新增後端欄位就能做出第一版（「準備度」先用該職缺最近 3 場報告平均分）。

| 區塊 | 內容 | 資料來源 |
|---|---|---|
| 問候列 | 下一場面試倒數、離 85 分還差幾場 | targets、reports |
| Ava 建議的下一步 | 一件事＋理由＋預估時間＋主按鈕 | `active_interview`、報告弱項 |
| 4 個數據卡 | 平均分數、完成面試、練習時數、待加強題目 | reports |
| 分數趨勢 | 最近 8 場總分折線＋「準備好 85」門檻線 | reports |
| 職缺準備度 | 每個職缺分數、狀態、進度條 | targets、reports |
| 待加強題目 | < 65 分的題目，可單題或全部加入題組 | report items、question sets |
| Ava 正在處理 | 評分、出題、解析的進度 | background jobs 狀態 |
| 能力落差 | 前 3 個缺口，連到能力落差分析 | career-plan |

## 4. Design Tokens

已存成 OpenPencil 變數（集合 `Color`、`Spacing & Radius`），可直接匯出成 CSS：

```css
:root {
  /* Neutral */
  --color-bg-canvas: #F6F6F3;   /* 頁面底 */
  --color-bg-surface: #FFFFFF;  /* 卡片 */
  --color-bg-subtle: #FAFAF8;   /* 側欄、表頭 */
  --color-bg-hover: #EFEFEB;    /* hover、目前頁 */
  --color-border-default: #E5E5E0;
  --color-border-strong: #D2D2CC;
  --color-text-primary: #1B1B19;
  --color-text-secondary: #5C5C57;
  --color-text-tertiary: #7A7A74;
  --color-text-inverse: #FFFFFF;
  /* Brand：主按鈕、目前位置、AI */
  --color-brand-default: #4A55C8;
  --color-brand-hover: #3D47B2;
  --color-brand-subtle: #EEEFFB;
  --color-brand-text: #3A44A8;
  /* Semantic：分數 ≥80 / 60–79 / <60 */
  --color-success-default: #1E7F55;  --color-success-subtle: #E7F4ED;
  --color-warning-default: #A15F00;  --color-warning-subtle: #FCF1DF;
  --color-danger-default: #BE3434;   --color-danger-subtle: #FAEAEA;
  /* Chart */
  --color-chart-1: #4A55C8; --color-chart-2: #1A9393; --color-chart-3: #D98324;
  --color-chart-grid: #ECECE7;
  /* Spacing（4px 基準） */
  --spacing-1: 4px;  --spacing-2: 8px;  --spacing-3: 12px; --spacing-4: 16px;
  --spacing-5: 20px; --spacing-6: 24px; --spacing-8: 32px; --spacing-10: 40px;
  --spacing-12: 48px; --spacing-16: 64px;
  /* Radius */
  --radius-sm: 4px;  /* badge */
  --radius-md: 6px;  /* 按鈕、輸入框、nav item */
  --radius-lg: 8px;  /* 小卡 */
  --radius-xl: 12px; /* 卡片 */
  --radius-full: 999px;
}
```

## 5. Typography

中文與內文用 **Noto Sans TC**；數字、分數、英文標籤用 **Inter**（tabular numbers）。

| Token | 大小／行高 | 字重 | 用途 |
|---|---|---|---|
| display | 28 / 36 | 600 | 頁面主標（問候） |
| h1 | 22 / 30 | 600 | 頁面標題 |
| h2 | 16 / 24 | 600 | 卡片標題（Dashboard 用 15） |
| body | 14 / 22 | 400 | 內文、題目 |
| small | 13 / 20 | 400 | 次要說明、按鈕文字 |
| caption | 12 / 16 | 500 | 區塊標籤、時間、表頭 |
| metric | Inter 28 / 34 | 600 | 數據卡數字 |

## 6. Layout

- 桌面版 1440：側欄 248、主區左右 32、卡片間距 16、區塊間距 24、卡片內距 20。
- 層次靠 1px 邊框與背景明度，**不用陰影**；只有浮層（選單、彈窗、tooltip）才加陰影。
- 圖示：Lucide，16px（nav、按鈕內 14px），線寬 1.5–2。

## 7. Components

| 元件 | 規格 |
|---|---|
| Button / Primary | 高 32（強調 36）、左右 12、radius 6、brand 底白字；hover 用 brand-hover |
| Button / Secondary | 白底＋border-default、primary 文字 |
| Button / Ghost | 無底，secondary 文字；hover 用 bg-hover |
| Button / Danger | danger-subtle 底＋danger 文字 |
| IconButton | 32×32、border-default |
| Badge | 左右 6、上下 1、radius 4、11/500；brand／success／warning／danger／neutral 五種 |
| Input | 高 32、radius 6、border-default；focus 改 2px brand 邊框 |
| NavItem | 高 32、radius 6、圖示 16；目前頁用 bg-hover＋primary 文字 500 |
| JobItem | 8px 圓點（準備度顏色）＋職稱 · 公司 |
| Card | 白底、border-default、radius 12、內距 20；標題 h2＋13px 說明 |
| StatCard | 標籤 13 secondary＋圖示、metric 數字、12px 變化（success/danger） |
| Progress | 高 6（AI 進度 4）、grid 底、radius full；顏色依分數規則 |
| 分數顏色規則 | ≥ 80 success · 60–79 warning · < 60 danger · AI 進行中 brand |

## 8. 下一步

1. 你確認 Dashboard 與 Design System（或提出修改）。
2. 確認後再畫下一頁，建議順序：面試間 → 面試報告 → 職缺頁。
3. 全部頁面確認後才開新分支實作。
