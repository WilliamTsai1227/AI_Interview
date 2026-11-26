/* forma. Independent design prototype. Demonstration data only; no production API calls. */
"use strict";
const $ = (q, root = document) => root.querySelector(q);
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (key === "class") node.className = value;
    else if (key === "value") node.value = value;
    else node.setAttribute(key, value);
  }
  for (const child of children.flat(Infinity))
    if (child != null)
      node.append(
        child instanceof Node ? child : document.createTextNode(String(child)),
      );
  return node;
}
const button = (text, attrs = {}) =>
  el("button", { type: "button", ...attrs }, text);
const link = (text, href, cls = "text-link") =>
  el("a", { href, class: cls }, text);
const jobs = [
  {
    id: "ai",
    company: "Anthropic",
    role: "AI Engineer",
    score: 72,
    weak: "系統設計",
    question:
      "如果要為一個 RAG 系統加入快取，你會如何設計快取的粒度與失效策略？",
  },
  {
    id: "backend",
    company: "LINE Taiwan",
    role: "Backend Engineer",
    score: 68,
    weak: "資料庫設計",
    question:
      "如果訂單服務同時收到重複請求，你會如何設計冪等性並確保資料一致？",
  },
  {
    id: "product",
    company: "Notion",
    role: "Product Designer",
    score: 81,
    weak: "設計決策",
    question:
      "請分享一個你透過使用者研究改變設計方向的案例。你如何判斷新的方向更好？",
  },
];
const tools = [
  {
    id: "interview",
    name: "模擬面試",
    en: "Interview practice",
    category: "面試練習",
    symbol: "circle",
    description:
      "讓 Ava 依照你的目標職缺提問，在一次次對話中，找到回答的節奏。",
    placeholder: "想特別練習哪個面向？",
  },
  {
    id: "resume",
    name: "履歷精修",
    en: "Resume studio",
    category: "履歷與表達",
    symbol: "stack",
    description:
      "把做過的事說得更準確。整理專案經驗，寫出有證據、有重點的履歷。",
    placeholder: "貼上想修改的一段履歷，例如：負責後端 API 開發與資料庫維護。",
  },
  {
    id: "gap",
    name: "能力落差分析",
    en: "Mind the gap",
    category: "職涯探索",
    symbol: "triangle",
    description: "對照職缺需求與目前的能力，找出值得投入時間的下一個方向。",
    placeholder:
      "描述你目前的技能與經驗，例如：熟悉 Python，做過 RAG 專案，想加強部署能力。",
  },
  {
    id: "questions",
    name: "專屬題庫",
    en: "Question collection",
    category: "面試練習",
    symbol: "arch",
    description: "從職缺與弱項出發，整理一份真正與你有關的練習題目。",
    placeholder: "想加強什麼？例如：RAG 架構設計、快取策略、行為面試。",
  },
  {
    id: "story",
    name: "經驗敘事",
    en: "Tell your story",
    category: "履歷與表達",
    symbol: "cross",
    description: "用 STAR 結構整理經歷，讓你的判斷、行動與影響更容易被理解。",
    placeholder: "描述一個你曾經解決的問題、採取的行動，以及最後的結果。",
  },
  {
    id: "plan",
    name: "準備路線",
    en: "A considered plan",
    category: "職涯探索",
    symbol: "lines",
    description: "把準備拆成可以實踐的小步驟，為重要的面試安排自己的節奏。",
    placeholder: "距離面試還有多久？目前最擔心什麼？",
  },
];
const initialHistory = [
  {
    id: "sample1",
    date: "2026-11-25",
    title: "AI Engineer · 系統設計",
    detail: "Anthropic · 5 題練習",
    kind: "模擬面試",
    score: 72,
    body: "示範報告：你能清楚說明 RAG 的檢索流程。下一步可以補充快取失效、資料更新與權限隔離的取捨。",
  },
  {
    id: "sample2",
    date: "2026-11-23",
    title: "讓專案經歷更具體",
    detail: "履歷內容整理",
    kind: "履歷精修",
    score: null,
    body: "示範修改：以「問題 → 技術選擇 → 驗證方式」整理專案經歷。請只使用實際做過的事與可驗證的成果。",
  },
  {
    id: "sample3",
    date: "2026-11-21",
    title: "AI Engineer · 技術基礎",
    detail: "Anthropic · 5 題練習",
    kind: "模擬面試",
    score: 65,
    body: "示範報告：基礎觀念清楚，回答可再加入實際案例與測試方法。",
  },
];
function readSession(key, fallback) {
  try {
    return JSON.parse(sessionStorage.getItem(key)) ?? fallback;
  } catch {
    return fallback;
  }
}
let history = readSession("forma-history", initialHistory);
if (!Array.isArray(history)) history = initialHistory;
let jobId = readSession("forma-job", "ai");
const currentJob = () => jobs.find((j) => j.id === jobId) || jobs[0];
let route = "",
  category = "全部工具",
  query = "",
  practice = null,
  toastTimer;
let activeTool = null,
  generationTimer = null,
  generating = false;
function persist() {
  try {
    sessionStorage.setItem("forma-history", JSON.stringify(history));
    sessionStorage.setItem("forma-job", JSON.stringify(jobId));
  } catch {}
}
function toast(message) {
  $("#toast").textContent = message;
  $("#toast").classList.add("visible");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => $("#toast").classList.remove("visible"), 2600);
}
function saveRecord(record) {
  const item = {
    id: globalThis.crypto?.randomUUID?.() || String(Date.now()),
    date: new Date().toLocaleDateString("en-CA"),
    ...record,
  };
  history.unshift(item);
  persist();
  return item;
}
function art() {
  return el(
    "figure",
    {
      class: "art-sheet",
      "aria-label": "品牌幾何構圖：橄欖灰與紙白半圓、朱紅斜軸，象徵整理與前進",
    },
    el("span", { class: "art-meta" }, "STUDIES IN PROGRESS / 01"),
    el("div", { class: "art-shape", "aria-hidden": "true" }),
    el("div", { class: "art-axis", "aria-hidden": "true" }),
    el(
      "figcaption",
      { class: "art-caption" },
      el("em", {}, "A work in progress."),
      el("span", {}, "forma. / 2026"),
    ),
  );
}
function jobSelect(onchange) {
  const select = el(
    "select",
    {
      id: "job-select",
      onchange: (e) => {
        jobId = e.target.value;
        persist();
        onchange?.();
      },
    },
    jobs.map((j) => el("option", { value: j.id }, `${j.company} / ${j.role}`)),
  );
  select.value = currentJob().id;
  return select;
}
function pageHead(en, title, description) {
  return el(
    "section",
    { class: "page-head" },
    el("div", {}, el("p", { class: "en-title" }, en), el("h1", {}, title)),
    el("p", {}, description),
  );
}
function overview() {
  const j = currentJob();
  return [
    el(
      "section",
      { class: "hero" },
      el(
        "div",
        { class: "hero-copy" },
        el(
          "div",
          { class: "kicker" },
          el("span", { class: "dot" }),
          "William，歡迎回到自己的節奏。",
        ),
        el(
          "h1",
          {},
          el("span", {}, "讓下一步，"),
          el("span", {}, "更有把握。"),
        ),
        el(
          "p",
          {},
          "準備面試，也是在整理自己。讓 Ava 陪你釐清經驗、練習表達，把每一次機會，變成有準備的選擇。",
        ),
        el(
          "div",
          { class: "hero-actions" },
          link("開始一段練習 ↗", "#practice", "button"),
          link("探索準備工具", "#tools"),
        ),
      ),
      art(),
    ),
    el(
      "section",
      { class: "context-bar", "aria-label": "目前準備的職缺" },
      el(
        "div",
        { class: "job-choice" },
        el("label", { for: "job-select" }, "目前的目標"),
        jobSelect(render),
      ),
      el("span", { class: "context-note" }, "不急著完美，先往前一步。"),
    ),
    el(
      "div",
      { class: "overview-grid" },
      el(
        "section",
        {},
        el(
          "div",
          { class: "section-heading" },
          el("h2", {}, "你的準備路線"),
          el("small", {}, "從理解，到表達。"),
        ),
        ...[
          {
            title: "釐清職缺，也看見自己",
            desc: "整理職缺需求，找出與你經驗的交集。",
            action: "查看落差",
            tool: "gap",
          },
          {
            title: "把經驗，說成好故事",
            desc: "從一個具體專案開始，整理你的判斷與影響。",
            action: "修改履歷",
            tool: "resume",
          },
          {
            title: "練習一次，再靠近一點",
            desc: `針對${j.weak}，進行一段有回饋的對話。`,
            action: "開始練習",
            tool: "interview",
          },
        ].map((step, i) =>
          el(
            "div",
            { class: "path-row" },
            el("span", { class: "path-no" }, String(i + 1).padStart(2, "0")),
            el("div", {}, el("h3", {}, step.title), el("p", {}, step.desc)),
            button(`${step.action} ↗`, {
              class: "row-action",
              onclick: () => openTool(step.tool),
            }),
          ),
        ),
      ),
      el(
        "aside",
        { class: "coach" },
        el(
          "div",
          { class: "coach-head" },
          el("span", {}, "Ava 的一點觀察"),
          el("span", { class: "coach-sign" }, "Ava."),
        ),
        el(
          "blockquote",
          {},
          j.id === "product"
            ? "「好的作品集，不只呈現最後的畫面，也讓人看見你為什麼這樣選擇。」"
            : `「你已經能說清楚技術概念。下一步，試著把${j.weak}的取捨，放進真實的專案情境。」`,
        ),
        el("p", {}, "依據最近一次練習的示範建議"),
        link("針對這個方向練習 ↗", "#practice"),
      ),
    ),
    el(
      "section",
      { class: "metrics", "aria-label": "準備概況，示範數據" },
      ...[
        [`${j.score}`, "目前準備度 / 100"],
        ["08", "已完成的練習"],
        ["03", "值得加強的方向"],
      ].map(([num, label]) =>
        el(
          "div",
          { class: "metric" },
          el("strong", {}, num),
          el("span", {}, label),
        ),
      ),
    ),
  ];
}
function drawTools() {
  const grid = $("#tool-grid");
  if (!grid) return;
  const shown = tools.filter(
    (t) =>
      (category === "全部工具" || t.category === category) &&
      `${t.name}${t.description}${t.en}`
        .toLowerCase()
        .includes(query.toLowerCase()),
  );
  grid.replaceChildren(
    ...shown.map((t) =>
      el(
        "article",
        { class: "tool" },
        el(
          "div",
          { class: "tool-top" },
          el("div", {
            class: `tool-symbol ${t.symbol}`,
            "aria-hidden": "true",
          }),
          el("span", { class: "tool-category" }, t.category),
        ),
        el("h2", {}, t.name),
        el("p", {}, t.description),
        button("開啟工具 ↗", {
          class: "row-action",
          onclick: () => openTool(t.id),
          "aria-label": `開啟${t.name}`,
        }),
      ),
    ),
  );
  if (!shown.length)
    grid.append(
      el(
        "div",
        { class: "empty" },
        "沒有找到相關工具。試試「履歷」、「面試」或清除搜尋。",
      ),
    );
}
function toolsPage() {
  return [
    pageHead(
      "The toolkit.",
      "為思考，找到工具。",
      "從一段履歷到一次對話，把準備拆成小事，找到此刻最需要的幫助。",
    ),
    el(
      "div",
      { class: "filters", "aria-label": "工具分類" },
      ...["全部工具", "面試練習", "履歷與表達", "職涯探索"].map((c) =>
        button(c, {
          class: "filter",
          "aria-pressed": String(category === c),
          onclick: (e) => {
            category = c;
            document
              .querySelectorAll(".filter")
              .forEach((b) =>
                b.setAttribute("aria-pressed", String(b === e.currentTarget)),
              );
            drawTools();
          },
        }),
      ),
      el("input", {
        class: "filter-search",
        type: "search",
        placeholder: "搜尋工具…",
        "aria-label": "搜尋準備工具",
        value: query,
        oninput: (e) => {
          query = e.target.value;
          drawTools();
        },
      }),
    ),
    el("section", {
      class: "tool-grid",
      id: "tool-grid",
      "aria-live": "polite",
      "aria-label": "準備工具",
    }),
  ];
}
const questions = (j) => [
  j.question,
  "請分享一個你需要在時間、品質與成本之間做取捨的專案。你如何做決定？",
  "當你的方案與團隊成員有不同意見時，你會如何釐清問題並推進工作？",
  "如果上線後的結果不如預期，你會如何定位問題並驗證修正？",
  "回顧剛才的回答，有哪一個部分你希望補充得更具體？",
];
let difficulty = "適中";
function practicePage() {
  if (practice) return stage();
  return [
    pageHead(
      "Room to practice.",
      "把想法，說清楚。",
      "這裡可以停頓、可以重來。先在沒有壓力的地方，練習你的下一次對話。",
    ),
    el(
      "div",
      { class: "workspace" },
      el(
        "form",
        {
          onsubmit: (e) => {
            e.preventDefault();
            practice = {
              index: 0,
              answers: [],
              job: { ...currentJob() },
              difficulty,
              total: Number($("#question-count").value),
              done: false,
            };
            render();
          },
        },
        el(
          "div",
          { class: "field" },
          el("label", { for: "job-select" }, "這次為哪個職缺準備？"),
          jobSelect(),
        ),
        el(
          "div",
          { class: "field" },
          el("label", { id: "difficulty-label" }, "練習的深度"),
          el(
            "div",
            {
              class: "segmented",
              role: "group",
              "aria-labelledby": "difficulty-label",
            },
            ...["暖身", "適中", "挑戰"].map((d) =>
              button(d, {
                "aria-pressed": String(d === difficulty),
                onclick: (e) => {
                  difficulty = d;
                  e.currentTarget.parentElement
                    .querySelectorAll("button")
                    .forEach((b) =>
                      b.setAttribute(
                        "aria-pressed",
                        String(b === e.currentTarget),
                      ),
                    );
                },
              }),
            ),
          ),
        ),
        el(
          "div",
          { class: "field" },
          el("label", { for: "question-count" }, "題數"),
          el(
            "select",
            { id: "question-count" },
            el("option", { value: "3" }, "3 題 / 一點點練習"),
            el("option", { value: "5" }, "5 題 / 完整對話"),
          ),
        ),
        el(
          "button",
          { class: "button", type: "submit" },
          "進入練習室",
          el("span", { "aria-hidden": "true" }, "↗"),
        ),
      ),
      el(
        "aside",
        { class: "practice-note" },
        el("span", { class: "coach-sign" }, "Ava."),
        el("h2", {}, "Think. Speak. Refine."),
        el(
          "p",
          {},
          "一次好的練習，不是答對所有問題，而是發現自己還能說得更好的地方。",
        ),
        el(
          "ul",
          {},
          el("li", {}, "依目標職缺安排技術與經驗問題"),
          el("li", {}, "用文字回答，依自己的節奏前進"),
          el("li", {}, "完成後，帶走一個具體的改善方向"),
        ),
        el(
          "p",
          {},
          "原型體驗：使用預設題目與示範回饋，不使用麥克風，也不會送出你的回答。難度選擇會調整問題引導。",
        ),
      ),
    ),
  ];
}
function stage() {
  const p = practice;
  if (p.done)
    return [
      el(
        "section",
        { class: "stage" },
        el("p", { class: "en-title" }, "A little more prepared."),
        el("h1", { class: "question" }, "完成這一段，也更認識自己。"),
        el(
          "div",
          { class: "score" },
          "— ",
          el("small", {}, "原型不進行真實評分"),
        ),
        el(
          "div",
          { class: "feedback" },
          el("h3", {}, "下一次，加入一個具體的例子。"),
          el(
            "p",
            {},
            "示範回饋：試著依照「情境 → 你的判斷 → 採取的行動 → 如何驗證結果」來組織回答。數據尚未經模型分析。",
          ),
        ),
        el(
          "div",
          { class: "stage-actions" },
          link("查看練習紀錄 ↗", "#history", "button"),
          button("再練一次", {
            class: "button secondary",
            onclick: () => {
              practice = null;
              render();
            },
          }),
        ),
      ),
    ];
  const question = questions(p.job)[p.index];
  const answer = el("textarea", {
    id: "answer",
    placeholder: "先說出你的想法，不必一次就完美。",
    maxlength: "6000",
    "aria-label": "你的回答",
  });
  const error = el("p", { class: "error", role: "alert" });
  const intro =
    p.difficulty === "暖身"
      ? "可以先用一個你熟悉的例子回答。"
      : p.difficulty === "挑戰"
        ? "請比較至少兩個方案，並說明失敗情境與驗證方法。"
        : "試著說明你的選擇，以及背後的原因。";
  return [
    el(
      "section",
      { class: "stage" },
      el(
        "div",
        { class: "stage-meta" },
        el("span", {}, `Ava / ${p.job.company} · ${p.job.role}`),
        el(
          "span",
          {},
          `問題 ${String(p.index + 1).padStart(2, "0")} / ${String(p.total).padStart(2, "0")}`,
        ),
      ),
      el(
        "div",
        {
          class: "progress",
          role: "progressbar",
          "aria-label": "面試進度",
          "aria-valuemin": "0",
          "aria-valuemax": String(p.total),
          "aria-valuenow": String(p.index),
        },
        el("span", { style: `width:${(p.index / p.total) * 100}%` }),
      ),
      el("h1", { class: "question" }, question),
      el("p", { class: "muted" }, intro),
      el("label", { for: "answer" }, "你的回答"),
      answer,
      error,
      el(
        "div",
        { class: "stage-actions" },
        button("離開這次練習", {
          class: "text-link",
          onclick: () => {
            practice = null;
            render();
            toast("已離開練習，未儲存回答");
          },
        }),
        button(p.index + 1 === p.total ? "完成這段練習 ↗" : "下一個問題 ↗", {
          class: "button",
          onclick: () => {
            if (answer.value.trim().length < 10) {
              error.textContent =
                "請至少寫下 10 個字，試著加入一個具體的想法。";
              answer.focus();
              return;
            }
            p.answers.push(answer.value.trim());
            p.index++;
            if (p.index === p.total) {
              p.done = true;
              saveRecord({
                title: `${p.job.role} · ${p.difficulty}練習`,
                detail: `${p.job.company} · ${p.total} 題（原型示範）`,
                kind: "模擬面試",
                score: null,
                body:
                  p.answers
                    .map(
                      (a, i) =>
                        `問題 ${i + 1}：${questions(p.job)[i]}\n\n你的回答：${a}`,
                    )
                    .join("\n\n────────\n\n") +
                  "\n\n示範建議：加入具體情境、你的判斷與驗證方法。",
              });
            }
            render();
          },
        }),
      ),
    ),
  ];
}
let historyFilter = "全部紀錄";
function historyPage() {
  const shown = history.filter(
    (h) => historyFilter === "全部紀錄" || h.kind === historyFilter,
  );
  return [
    pageHead(
      "Notes on progress.",
      "每一步，都算數。",
      "回看曾經的想法，找到現在的進步。這裡保留你的練習與整理。",
    ),
    el(
      "div",
      { class: "filters" },
      ...["全部紀錄", "模擬面試", "履歷精修", "能力落差分析"].map((c) =>
        button(c, {
          class: "filter",
          "aria-pressed": String(c === historyFilter),
          onclick: () => {
            historyFilter = c;
            render();
          },
        }),
      ),
    ),
    el(
      "section",
      { class: "history-list", "aria-label": "練習紀錄" },
      el(
        "div",
        { class: "history-row history-head", "aria-hidden": "true" },
        "日期",
        "練習內容",
        el("span", { class: "history-kind" }, "類型"),
        "分數",
      ),
      ...shown.map((h) =>
        el(
          "article",
          { class: "history-row" },
          el("time", { datetime: h.date }, h.date.slice(5).replace("-", " / ")),
          el(
            "div",
            {},
            button(h.title + " ↗", {
              class: "row-action",
              onclick: () => showRecord(h),
            }),
            el("p", {}, h.detail),
          ),
          el("span", { class: "history-kind" }, h.kind),
          el(
            "span",
            {
              class: "history-score",
              "aria-label": h.score == null ? "未評分" : `${h.score} 分`,
            },
            h.score ?? "—",
          ),
        ),
      ),
      !shown.length
        ? el(
            "p",
            { class: "empty" },
            "這裡還沒有紀錄。到準備工具完成一次練習，就會出現在這裡。",
          )
        : null,
    ),
    el(
      "p",
      { class: "muted", style: "font-size:11px;margin-bottom:30px" },
      "前三筆為示範資料；新增紀錄只保留在目前分頁，關閉分頁後即清除。",
    ),
  ];
}
function identityPage() {
  return [
    pageHead(
      "FORMA. — VISUAL IDENTITY",
      "留白，讓想法成形。",
      "2026 / VOL.01。以瑞士平面設計的秩序，容納每個人不一樣的準備節奏。",
    ),
    el(
      "section",
      { class: "identity-section" },
      el("h2", {}, "色彩 / Colour"),
      el(
        "div",
        { class: "swatches" },
        ...[
          ["Paper", "#F4F3ED", "頁面與留白"],
          ["Ink", "#24251F", "文字與結構"],
          ["Olive", "#77796E", "輔助資訊與幾何"],
          ["Vermilion", "#BD442E", "行動與品牌識別"],
        ].map(([name, hex, usage]) =>
          el(
            "div",
            {},
            el("div", { class: "swatch", style: `background:${hex}` }),
            el("h3", {}, name),
            el("p", {}, `${hex} / ${usage}`),
          ),
        ),
      ),
    ),
    el(
      "section",
      { class: "identity-section" },
      el("h2", {}, "字體 / Typography"),
      el(
        "div",
        { class: "type-specimens" },
        el(
          "div",
          {},
          el("div", { class: "serif-specimen" }, "forma. Aa"),
          el("p", {}, "Georgia / 品牌與編輯字體"),
          el("p", {}, "Noto Serif TC / Ava 的觀察與面試提問"),
        ),
        el(
          "div",
          {},
          el("div", { class: "sans-specimen" }, "把想法，說清楚。"),
          el("p", {}, "DM Sans + Noto Sans TC / 標題、導覽與操作"),
          el("p", {}, "標題 48–86 px · 內文 12–14 px · 輔助資訊 10–12 px"),
        ),
      ),
    ),
    el(
      "section",
      { class: "identity-section identity-art" },
      art(),
      el(
        "div",
        {},
        el("h2", {}, "幾何 / A considered balance"),
        el(
          "p",
          {},
          "半圓代表尚在整理的經驗，斜軸代表前進的方向。理性的框架與不對稱的構圖，讓準備過程保留人的節奏。",
        ),
        el(
          "p",
          {},
          "直角、細線、低密度。朱紅只標記主要行動與當前位置；內容區不使用陰影或漸層。",
        ),
        link("回到總覽 ↗", "#overview"),
      ),
    ),
  ];
}
function openTool(id) {
  if (id === "interview") {
    if (location.hash === "#practice") {
      practice = null;
      render();
    } else location.hash = "practice";
    return;
  }
  activeTool = tools.find((t) => t.id === id);
  const t = activeTool;
  if (!t) return;
  const d = $("#tool-dialog");
  const model = el(
    "select",
    { id: "model" },
    el("option", { value: "balanced" }, "均衡 / 清楚、具體"),
    el("option", { value: "deep" }, "深入 / 多想一層"),
  );
  const input = el("textarea", {
    id: "prompt",
    rows: "5",
    maxlength: "6000",
    placeholder: t.placeholder,
  });
  const error = el("p", { class: "error", role: "alert" });
  const submit = button("整理我的想法 ↗", {
    class: "button",
    onclick: () => {
      if (input.value.trim().length < 10) {
        error.textContent = "請至少輸入 10 個字，讓這次整理有一個具體的起點。";
        input.focus();
        return;
      }
      error.textContent = "";
      generating = true;
      submit.disabled = true;
      submit.textContent = "Ava 正在整理…";
      const prompt = input.value.trim(),
        mode = model.value;
      generationTimer = setTimeout(() => {
        generationTimer = null;
        generating = false;
        if (!d.open) return;
        const result = generate(t, prompt, mode);
        const record = saveRecord({
          title: t.name + " · " + currentJob().role,
          detail: currentJob().company + " · 原型示範輸出",
          kind: t.name,
          score: null,
          body: result,
        });
        showResult(t, result, record);
      }, 900);
    },
  });
  d.replaceChildren(
    el(
      "div",
      { class: "dialog-head" },
      el("h2", { id: "tool-title" }, t.name),
      button("✕", {
        class: "icon-button",
        "aria-label": "關閉工具",
        onclick: () => d.close(),
      }),
    ),
    el("p", {}, t.description),
    el("label", { for: "model" }, "思考方式"),
    model,
    el("label", { for: "prompt" }, "從你的想法開始"),
    input,
    error,
    el(
      "p",
      { style: "font-size:10px;margin-top:12px" },
      "原型體驗：兩種思考方式使用不同的預設示範內容，未呼叫 AI 模型。",
    ),
    el("div", { class: "dialog-actions" }, submit),
  );
  if (!d.open) d.showModal();
  input.focus();
}
function generate(t, prompt, mode) {
  const subject = prompt.slice(0, 110) + (prompt.length > 110 ? "…" : "");
  const base = {
    resume:
      "試著這樣整理：\n\n在［具體情境］中，負責［你的工作範圍］，選擇［實際採用的方法］來解決［問題］，並透過［可驗證的結果］確認影響。\n\n保留你實際做過的事；沒有數據時，可以描述驗證方法，不必補上不存在的數字。",
    gap: `對照 ${currentJob().role}，可以先確認三個面向：\n\n• 你能獨立完成哪些工作？用一個專案作為證據。\n• 哪些需求只有概念，還缺少實作？安排一個小型驗證。\n• 哪些能力在面試時不容易說清楚？先寫出技術選擇與取捨。`,
    questions: `先練習這三題：\n\n1. ${currentJob().question}\n\n2. 分享一個你做過的重要取捨，你比較了哪些替代方案？\n\n3. 你如何知道自己的方案有效？請說明你觀察的證據。`,
    story:
      "把故事分成四個部分：\n\n情境 / 當時發生什麼事？\n任務 / 你需要解決什麼問題？\n行動 / 你本人做了什麼決定與工作？\n結果 / 你如何確認成效？下一次會改變什麼？\n\n先刪去與你的判斷無關的背景，讓行動成為故事的重心。",
    plan: "給自己一個可以執行的準備節奏：\n\n第一步 / 對照職缺，挑出三個最重要的需求。\n第二步 / 為每個需求整理一個實際案例。\n第三步 / 用一次模擬面試，確認哪裡還說不清楚。\n第四步 / 回看回饋，只選一個方向再練一次。",
  }[t.id];
  return (
    `你的起點：${subject}\n\n${base}` +
    (mode === "deep"
      ? "\n\n多想一層 / 如果面試官追問「為什麼不是另一個方案」，你會如何說明？列出一個替代選擇，以及你沒有採用它的原因。"
      : "")
  );
}
function showResult(t, result, record) {
  const d = $("#tool-dialog");
  d.replaceChildren(
    el(
      "div",
      { class: "dialog-head" },
      el("h2", { id: "tool-title" }, t.name),
      button("✕", {
        class: "icon-button",
        "aria-label": "關閉結果",
        onclick: () => d.close(),
      }),
    ),
    el("span", { class: "result-label" }, "Ava / 示範整理"),
    el("div", { class: "result-copy" }, result),
    el(
      "p",
      {},
      "已加入這個分頁的練習紀錄。這是預設示範輸出，不代表真實 AI 分析。",
    ),
    el(
      "div",
      { class: "dialog-actions" },
      button("複製內容", {
        class: "button secondary",
        onclick: async () => {
          try {
            await navigator.clipboard.writeText(result);
            toast("已複製內容");
          } catch {
            toast("無法自動複製，請選取結果文字後複製");
          }
        },
      }),
      button("再整理一次", {
        class: "button secondary",
        onclick: () => openTool(t.id),
      }),
      link("查看紀錄 ↗", "#history", "button"),
    ),
  );
  d.querySelector("a").addEventListener("click", () => {
    d.close();
    if (route === "history") render();
  });
}
function showRecord(record) {
  const d = $("#tool-dialog");
  d.replaceChildren(
    el(
      "div",
      { class: "dialog-head" },
      el("h2", { id: "tool-title" }, record.title),
      button("✕", {
        class: "icon-button",
        "aria-label": "關閉紀錄",
        onclick: () => d.close(),
      }),
    ),
    el("p", {}, record.date + " / " + record.kind),
    el("div", { class: "result-copy" }, record.body),
  );
  if (!d.open) d.showModal();
}
function searchResults() {
  const q = $("#global-search").value.trim().toLowerCase();
  const shown = tools.filter((t) =>
    `${t.name}${t.description}${t.en}`.toLowerCase().includes(q),
  );
  $("#search-results").replaceChildren(
    ...shown.map((t) =>
      button([el("span", {}, t.name), el("small", {}, t.category + " ↗")], {
        class: "search-result",
        onclick: () => {
          $("#search-dialog").close();
          openTool(t.id);
        },
      }),
    ),
  );
  if (!shown.length)
    $("#search-results").append(
      el("p", { class: "empty" }, "找不到相關工具，試試其他關鍵字。"),
    );
}
function render() {
  const oldRoute = route;
  route = location.hash.slice(1) || "overview";
  if (
    !["overview", "tools", "practice", "history", "identity"].includes(route)
  ) {
    location.hash = "overview";
    return;
  }
  if (
    oldRoute === "practice" &&
    route !== "practice" &&
    practice &&
    !practice.done
  )
    practice = null;
  const pages = {
    overview,
    tools: toolsPage,
    practice: practicePage,
    history: historyPage,
    identity: identityPage,
  };
  $("#main").replaceChildren(...pages[route]());
  if (route === "tools") drawTools();
  document.querySelectorAll("#navigation a").forEach((a) => {
    if (a.hash === "#" + route) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
  const names = {
    overview: "總覽",
    tools: "準備工具",
    practice: "模擬面試",
    history: "練習紀錄",
    identity: "視覺規範",
  };
  document.title = `${names[route]} — forma.`;
  $("#navigation").classList.remove("open");
  $(".menu-toggle").setAttribute("aria-expanded", "false");
  if (oldRoute !== route) {
    window.scrollTo({ top: 0, behavior: "instant" });
    $("#main").focus({ preventScroll: true });
  }
}
$(".menu-toggle").addEventListener("click", () => {
  const open = $("#navigation").classList.toggle("open");
  $(".menu-toggle").setAttribute("aria-expanded", String(open));
});
function openSearch() {
  const d = $("#search-dialog");
  if (d.open) return;
  d.showModal();
  searchResults();
  $("#global-search").focus();
}
$(".search-toggle").addEventListener("click", openSearch);
$("#global-search").addEventListener("input", searchResults);
$(".account").addEventListener("click", () => $("#about-dialog").showModal());
$(".close-about").addEventListener("click", () => $("#about-dialog").close());
for (const b of document.querySelectorAll(".close-dialog"))
  b.addEventListener("click", () => b.closest("dialog").close());
$("#tool-dialog").addEventListener("close", () => {
  if (generationTimer) {
    clearTimeout(generationTimer);
    generationTimer = null;
  }
  generating = false;
  if (route === "history") render();
});
window.addEventListener("keydown", (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
    e.preventDefault();
    if (!document.querySelector("dialog[open]")) openSearch();
  }
});
window.addEventListener("hashchange", render);
render();
