// 履歷修改（spec/features/resume-revision.md）：像語言學習 App 開始前的卡片，一關一關問。
// #revise：首頁（開始、歷次修改）→ 設定卡片（目的、履歷、確認、職缺、輸出偏好）→ 建立後到 #revise/{id}
// #revise/{id}：Ava 產生挖掘卡片 → 逐張回答（每答一張就存）→ 產生新版履歷 → 逐條接受／還原／編輯 → Word、複製、存成新履歷
import { get, getBlob, post, put } from "../api.js";
import { openJd } from "../components/jd.js";
import { openSettings } from "../components/settings.js";
import { dots, h, icon, replace } from "../dom.js";
import { fmtDate, loadResumes, loadTargets, S } from "../store.js";
import { copyText, errorToast, openMenu, seg, setHead, toast } from "../ui.js";

const GOALS = [
  ["target", "投特定職缺", "針對想應徵的職缺調整重點"],
  ["refresh", "整體更新", "補上最近的經歷，讓內容更完整"],
  ["switch", "轉職", "把過去的經驗轉成新領域看得懂的語言"],
  ["first", "第一份工作", "沒什麼工作經歷，想突顯專案和學習"],
];
const GOAL_NAME = Object.fromEntries(GOALS.map(([k, n]) => [k, n]));
const STATUS = { carding: "Ava 正在準備問題", answering: "回答中", generating: "產生中", ready: "已完成", failed: "沒有成功" };
const MAX_JOBS = 3;
const POLL_MS = 2000;
const TAG = { new: "新增", edit: "改寫" };
const jobName = (j) => `${j.company_name || "未知公司"}｜${j.title || "未命名職缺"}`;
const answered = (a) => !!a && (a.skipped || !!a.choice || !!(a.text || "").trim());
const PLACEHOLDER = /\[[^[\]\n]{1,40}\]/g;

export async function render(view, id, ctx) {
  setHead("pencil", "履歷修改");
  const wrap = h("div", { class: "wrap rv-wrap" }, h("div", { class: "center-load" }, dots()));
  view.append(wrap);
  let poll = 0;
  const stop = () => clearTimeout(poll);
  if (id) await revision(wrap, id, ctx, (fn) => { stop(); poll = setTimeout(fn, POLL_MS); });
  else await home(wrap, ctx);
  return stop;
}

// ---------- 共用：一張卡片 ----------
function progress(i, n) {
  return h("div", { class: "rv-prog", role: "progressbar", "aria-valuemin": 0, "aria-valuemax": n, "aria-valuenow": i },
    h("i", { style: `width:${n ? Math.round((Math.min(i + 1, n) / n) * 100) : 0}%` }));     // 第一張就有一點進度，看得出這是第幾關
}

function card({ step, total, phase, context, question, body, back, foot }) {
  return h("div", { class: "rv" },
    h("div", { class: "rv-top" },
      back ? h("button", { class: "ib", type: "button", "aria-label": "上一題", onclick: back }, icon("left")) : h("span", { class: "rv-sp" }),
      progress(step, total),
      h("span", { class: "faint num rv-cnt" }, `${Math.min(step + 1, total)} / ${total}`)),
    h("div", { class: "rv-card" },
      phase ? h("div", { class: "rv-phase" }, phase) : null,
      context ? h("p", { class: "rv-ctx" }, context) : null,
      h("h2", { class: "rv-q" }, question),
      body),
    foot ? h("div", { class: "rv-foot" }, foot) : null);
}

function option(label, sub, on, onclick, multi = false) {
  return h("button", { class: `rv-opt${on ? " on" : ""}`, type: "button", "aria-pressed": String(on), onclick },
    multi ? h("span", { class: "rv-box", "aria-hidden": "true" }, on ? icon("check", 14) : null) : null,
    h("span", { class: "bd" }, h("b", null, label), sub ? h("small", null, sub) : null));
}

const loadingCard = (title, text) => h("div", { class: "rv" }, h("div", { class: "rv-card rv-wait" },
  h("div", { class: "rv-spin" }, dots()), h("h2", { class: "rv-q" }, title), h("p", { class: "faint" }, text)));

// ---------- 首頁 ----------
async function home(wrap, ctx) {
  let items = [];
  try { ({ items } = await get("/resume-revisions")); } catch { /* 歷史只是輔助 */ }
  if (!ctx.isCurrent()) return;
  const doing = items.find((x) => x.status === "answering" || x.status === "carding");
  replace(wrap,
    h("div", { class: "rv-hero" },
      h("h1", null, "把你做過的事，寫進履歷"),
      h("p", { class: "muted" }, "Ava 會比對你的履歷和想投的職缺，一題一題問出你做過、但履歷沒寫好的事，最後產生新版履歷。大多點一下就能回答，約 3–5 分鐘。"),
      h("div", { class: "row", style: "justify-content:center;margin-top:22px" },
        h("button", { class: "btn pri xl", type: "button", onclick: () => setup(wrap, ctx) }, "開始修改", icon("right")),
        doing ? h("a", { class: "btn out xl", href: `#revise/${doing.id}` }, "繼續上次") : null)),
    items.length ? h("section", { class: "rv-hist" }, h("h2", null, "歷次修改"),
      h("div", { class: "rv-list" }, items.map((x) => h("a", { class: "rv-item", href: `#revise/${x.id}` },
        h("span", { class: "bd" },
          h("b", null, x.companies.length ? x.companies.join("、") : GOAL_NAME[x.goal] || "整體更新"),
          h("small", { class: "faint" }, [fmtDate(x.created_at), x.resume, x.status === "answering" ? `已答 ${x.answered}/${x.card_count}` : STATUS[x.status]]
            .filter(Boolean).join("・"))),
        icon("right", 16))))) : null);
}

// ---------- 階段 A：設定 ----------
async function setup(wrap, ctx) {
  replace(wrap, h("div", { class: "center-load" }, dots()));
  await Promise.all([loadResumes().catch(() => {}), loadTargets().catch(() => {})]);
  if (!ctx.isCurrent()) return;
  const f = { goal: null, resumeId: (S.resumes || []).find((r) => r.is_primary && r.parse_status === "parsed")?.id || null,
    confirm: null, correction: "", picked: new Set(), language: "zh", length: "one" };
  const details = new Map();       // 履歷 id → 解析結果（確認卡用）
  const STEPS = 5;
  let step = 0, busy = false;
  const go = (n) => { step = n; draw(); };

  function draw() {
    if (!ctx.isCurrent()) return;
    const back = step ? () => go(step - 1) : () => home(wrap, ctx);
    const next = (ok, label = "下一步", fn = () => go(step + 1)) =>
      h("button", { class: "btn pri", type: "button", disabled: !ok || busy, onclick: fn }, label, icon("right"));
    const base = { step, total: STEPS, phase: "設定", back };

    if (step === 0) {
      replace(wrap, card({ ...base, question: "這次修改履歷是為了什麼？",
        body: h("div", { class: "rv-opts" }, GOALS.map(([k, n, sub]) => option(n, sub, f.goal === k, () => { f.goal = k; setTimeout(() => go(1), 180); draw(); }))) }));
    } else if (step === 1) {
      const list = S.resumes || [];
      replace(wrap, card({ ...base, question: "要修改哪一份履歷？",
        body: list.length ? h("div", { class: "rv-opts" }, list.map((r) => {
          const ok = r.parse_status === "parsed";
          const b = option(`${r.filename}${r.is_primary ? "（主要）" : ""}`, ok ? null : r.parse_status === "failed" ? "解析失敗，不能使用" : "Ava 還在解析",
            f.resumeId === r.id, () => { if (!ok) return; if (f.resumeId !== r.id) { f.confirm = null; f.correction = ""; } f.resumeId = r.id; setTimeout(() => go(2), 180); draw(); });
          b.disabled = !ok;
          return b;
        })) : h("div", { class: "rv-empty" }, h("p", { class: "muted" }, "還沒有履歷。先上傳一份，Ava 解析完就能開始。"),
          h("button", { class: "btn out", type: "button", onclick: () => openSettings("resume", { onChanged: async () => { await loadResumes().catch(() => {}); draw(); } }) }, icon("upload"), "上傳履歷")),
        foot: [h("span"), next(!!f.resumeId)] }));
    } else if (step === 2) {
      const d = details.get(f.resumeId);
      if (!d) {
        replace(wrap, loadingCard("Ava 正在讀你的履歷", ""));
        get(`/resumes/${f.resumeId}`).then((r) => { details.set(f.resumeId, r.parsed || {}); draw(); })
          .catch((e) => { errorToast(e); go(1); });
        return;
      }
      const facts = [
        d.years_experience ? `${d.years_experience} 年工作經驗` : null,
        (d.experiences || []).slice(0, 2).map((e) => [e.title, e.org].filter(Boolean).join("｜")).filter(Boolean).join("、") || null,
        (d.skills || []).length ? `技能：${d.skills.slice(0, 6).join("、")}` : null,
      ].filter(Boolean);
      const ta = h("textarea", { class: "inp", rows: 3, maxlength: 500, placeholder: "例如：我現在是資深工程師，不是初階；Go 我只是學過",
        oninput: (e) => { f.correction = e.target.value; drawFoot(); } }, f.correction);
      const footBox = h("div", { class: "rv-foot-in" });
      const drawFoot = () => replace(footBox, h("span"), next(f.confirm === "ok" || (f.confirm === "fix" && f.correction.trim())));
      replace(wrap, card({ ...base, question: "Ava 讀到的你，對嗎？",
        body: [
          h("div", { class: "rv-facts" }, h("b", null, d.name || "你"), d.summary ? h("p", null, d.summary) : null,
            facts.length ? h("ul", null, facts.map((x) => h("li", null, x))) : null),
          h("div", { class: "rv-opts two" },
            option("對，沒錯", null, f.confirm === "ok", () => { f.confirm = "ok"; f.correction = ""; setTimeout(() => go(3), 180); draw(); }),
            option("有地方不對", null, f.confirm === "fix", () => { f.confirm = "fix"; draw(); setTimeout(() => wrap.querySelector("textarea")?.focus()); })),
          f.confirm === "fix" ? h("label", { class: "rv-detail" }, h("span", null, "哪裡不對？"), ta) : null,
        ],
        foot: footBox }));
      drawFoot();
    } else if (step === 3) {
      const list = S.targets || [];
      for (const t of [...f.picked]) if (!list.some((x) => x.id === t)) f.picked.delete(t);
      const full = f.picked.size >= MAX_JOBS;
      replace(wrap, card({ ...base, question: "要投哪個職缺？",
        context: f.goal === "target" ? "選了職缺，Ava 會依職缺的要求去問、去改。" : `選填。選了才會依職缺修；不選就做整體改善。最多 ${MAX_JOBS} 個。`,
        body: [
          list.length ? h("div", { class: "rv-opts" }, list.map((t) => {
            const on = f.picked.has(t.id);
            const pending = t.job.parse_status === "pending" || t.job.parse_status === "parsing";
            const b = option(jobName(t.job), pending ? "Ava 還在整理這個職缺" : [t.job.city, t.job.salary_text].filter(Boolean).join("・") || null, on,
              () => { on ? f.picked.delete(t.id) : f.picked.add(t.id); draw(); }, true);
            b.disabled = pending || (!on && full);
            return b;
          })) : h("p", { class: "faint" }, "還沒有職缺。"),
          h("button", { class: "linkb", type: "button", style: "margin-top:10px", onclick: () => openJd({ after: async (t) => {
            await loadTargets().catch(() => {});
            if (f.picked.size < MAX_JOBS) f.picked.add(t.id);
            draw();
          } }) }, icon("plus", 14), "貼上新職缺"),
        ],
        foot: [h("span", { class: "faint num" }, f.picked.size ? `已選 ${f.picked.size}/${MAX_JOBS}` : ""),
          next(true, f.picked.size ? "下一步" : "不選，整體改善")] }));
    } else {
      const start = async () => {
        busy = true; draw();
        try {
          const r = await post("/resume-revisions", { resume_id: f.resumeId, target_ids: [...f.picked], goal: f.goal || "refresh",
            language: f.language, length: f.length, correction: f.confirm === "fix" ? f.correction : "" });
          location.hash = `#revise/${r.id}`;
        } catch (e) { busy = false; errorToast(e); draw(); }
      };
      replace(wrap, card({ ...base, question: "新版履歷要怎麼呈現？",
        body: h("div", { class: "rv-prefs" },
          h("div", { class: "rv-pref" }, h("span", null, "語言"), seg([["zh", "中文"], ["en", "English"]], f.language, (v) => { f.language = v; draw(); }, "語言")),
          h("div", { class: "rv-pref" }, h("span", null, "篇幅"), seg([["one", "一頁"], ["two", "兩頁"]], f.length, (v) => { f.length = v; draw(); }, "篇幅"))),
        foot: [h("span"), next(true, busy ? "建立中⋯" : "開始問我問題", start)] }));
    }
  }
  draw();
}

// ---------- #revise/{id} ----------
async function revision(wrap, id, ctx, later) {
  let r;
  try { r = await get(`/resume-revisions/${id}`); } catch (e) {
    if (ctx.isCurrent()) replace(wrap, h("h1", null, "找不到這次修改"), h("p", { class: "muted" }, e.message), h("a", { class: "btn out", href: "#revise" }, "回到履歷修改"));
    return;
  }
  let idx = null;        // 目前在第幾張卡；null＝接著第一張還沒答的

  const refresh = () => later(async () => {
    if (!ctx.isCurrent()) return;
    try { r = await get(`/resume-revisions/${id}`); } catch { /* 下次再試 */ }
    draw();
  });

  let waiting = null;      // 目前顯示中的等待畫面（carding／generating）
  function draw() {
    if (!ctx.isCurrent()) return;
    // 等待中每 2 秒查一次狀態：狀態沒變就不重畫，否則卡片的進場動畫會一直重播、畫面一直跳
    if (r.status === waiting) return refresh();
    waiting = r.status === "carding" || r.status === "generating" ? r.status : null;
    if (r.status === "carding") { replace(wrap, loadingCard("Ava 正在準備問題", r.jobs.length ? `正在比對你的履歷和 ${r.jobs.map((j) => j.company_name).join("、")} 的職缺要求⋯` : "正在看你的履歷哪裡可以寫得更好⋯")); return refresh(); }
    if (r.status === "generating") { replace(wrap, loadingCard("Ava 正在寫新版履歷", "依你的回答補進履歷、改寫成果，約 30–60 秒。")); return refresh(); }
    if (r.status === "failed") {
      replace(wrap, h("div", { class: "rv" }, h("div", { class: "rv-card rv-wait" },
        h("h2", { class: "rv-q" }, r.cards ? "新版履歷沒有產生成功" : "問題沒有準備成功"),
        h("p", { class: "faint" }, r.error || "請再試一次。你的回答都還在。"),
        h("div", { class: "row", style: "justify-content:center;margin-top:16px" },
          h("button", { class: "btn pri", type: "button", onclick: () => regen() }, icon("redo"), "再試一次")))));
      return;
    }
    if (r.status === "answering") return drawCard();
    return result(wrap, r, ctx, (x) => { r = x; });
  }

  async function regen() {
    try { r = await post(`/resume-revisions/${id}/generate`); draw(); } catch (e) { errorToast(e); }
  }

  // ---------- 階段 B：挖掘卡片 ----------
  function drawCard() {
    const cards = r.cards || [];
    const n = cards.length;
    if (idx === null) { idx = cards.findIndex((_, i) => !answered(r.answers[i])); if (idx < 0) idx = n; }
    if (idx >= n) return drawDone();
    const c = cards[idx];
    const prev = r.answers[idx] || {};
    const a = { choice: prev.skipped ? null : prev.choice || null, text: prev.text || "" };
    let saving = false;
    const opt = () => c.options.find((o) => o.label === a.choice);
    const showDetail = () => !c.options.length || !!opt()?.opens_detail;
    const ta = h("textarea", { class: "inp", rows: 3, maxlength: 1000, placeholder: c.example ? `例如：${c.example}` : "",
      oninput: (e) => { a.text = e.target.value; drawFoot(); } }, a.text);
    const detail = h("label", { class: "rv-detail" }, h("span", null, c.detail_prompt || "說說看"), ta);
    const opts = h("div", { class: "rv-opts" });
    const footBox = h("div", { class: "rv-foot-in" });

    async function save(ans) {
      if (saving) return;
      saving = true;
      try {
        r = await put(`/resume-revisions/${id}/answers`, { answers: { ...r.answers, [idx]: ans } });
        idx += 1;
        drawCard();
      } catch (e) { saving = false; errorToast(e); }
    }
    const canNext = () => (c.options.length ? !!a.choice : !!a.text.trim());     // 細節選填：只選選項也能下一題
    function drawOpts() {
      replace(opts, c.options.map((o) => option(o.label, null, a.choice === o.label, () => {
        a.choice = o.label;
        drawOpts();
        detail.hidden = !showDetail();
        drawFoot();
        if (o.opens_detail) setTimeout(() => ta.focus());
        else setTimeout(() => save({ choice: a.choice, text: "", skipped: false }), 200);     // 不用補細節的選項：點了就下一題
      })));
    }
    function drawFoot() {
      replace(footBox,
        h("button", { class: "linkb", type: "button", onclick: () => save({ choice: null, text: "", skipped: true }) }, "跳過"),
        h("button", { class: "btn pri", type: "button", disabled: !canNext(), onclick: () => save({ choice: a.choice, text: showDetail() ? a.text : "", skipped: false }) },
          idx === n - 1 ? "完成" : "下一題", icon("right")));
    }
    drawOpts();
    detail.hidden = !showDetail();
    replace(wrap, card({ step: idx, total: n, phase: { gap: "職缺要求", quantify: "補上數字", clarify: "說清楚角色", missing: "可能漏寫的" }[c.kind],
      context: c.context, question: c.question, back: idx ? () => { idx -= 1; drawCard(); } : null,
      body: [c.options.length ? opts : null, detail, showDetail() && a.text.trim().length < 2 && c.options.length
        ? h("p", { class: "faint rv-hint" }, "可以只寫幾個字，Ava 會幫你整理成履歷的寫法；沒有數字就寫大概。") : null],
      foot: footBox }));
    drawFoot();
    if (!c.options.length) setTimeout(() => ta.focus());
  }

  function drawDone() {
    const cards = r.cards || [];
    const skipped = cards.filter((_, i) => r.answers[i]?.skipped).length;
    const said = cards.length - skipped;
    replace(wrap, card({ step: cards.length, total: cards.length, phase: "完成", question: "都問完了！",
      back: () => { idx = cards.length - 1; drawCard(); },
      body: [
        h("p", { class: "muted" }, `你回答了 ${said} 題${skipped ? `、跳過 ${skipped} 題` : ""}。Ava 會把你的回答補進履歷、改寫成果；沒給的數字會留成 [待補] 讓你填，不會自己編。`),
        r.jobs.length ? h("p", { class: "faint" }, `依職缺修改：${r.jobs.map(jobName).join("、")}`) : h("p", { class: "faint" }, "沒有選職缺：做整體改善。"),
      ],
      foot: [h("span"), h("button", { class: "btn pri", type: "button", onclick: () => regen() }, "產生新版履歷", icon("right"))] }));
  }

  draw();
}

// ---------- 階段 C：新版履歷 ----------
function finalDoc(res, dec) {
  const sections = [];
  for (const s of res.sections) {
    const entries = [];
    for (const e of s.entries) {
      const lines = [];
      for (const ln of e.lines) {
        const d = dec[ln.id] || {};
        let text;
        if (d.accept === false) {
          if (ln.change === "new") continue;
          text = ln.change === "edit" ? ln.original : ln.text;
        } else text = d.text || ln.text;
        if (text.trim()) lines.push(text.trim());
      }
      if (lines.length || e.title) entries.push({ title: e.title, meta: e.meta, lines });
    }
    if (entries.length) sections.push({ title: s.title, entries });
  }
  return { ...res, sections };
}

function plainText(doc) {
  const out = [doc.name, doc.headline, doc.contact].filter(Boolean);
  for (const s of doc.sections) {
    out.push("", s.title);
    for (const e of s.entries) {
      if (e.title || e.meta) out.push([e.title, e.meta].filter(Boolean).join("　"));
      const flat = !e.title && e.lines.length === 1;
      for (const l of e.lines) out.push(flat ? l : `• ${l}`);
    }
  }
  return out.join("\n");
}

function result(wrap, r, ctx, setR) {
  const res = r.result;
  let dec = { ...r.decisions };
  let editing = null;
  const counts = { new: 0, edit: 0 };
  res.sections.forEach((s) => s.entries.forEach((e) => e.lines.forEach((l) => { if (counts[l.change] !== undefined) counts[l.change] += 1; })));
  const docBox = h("div", { class: "rv-doc" });
  const headBox = h("div");

  async function saveDec() {
    try { const x = await put(`/resume-revisions/${r.id}/decisions`, { decisions: dec }); setR(x); r = x; drawHead(); } catch (e) { errorToast(e); }
  }
  const setDec = (lid, d) => {
    const next = { ...(dec[lid] || {}), ...d };
    if (next.accept !== false && !next.text) delete dec[lid]; else dec[lid] = next;
    editing = null;
    drawDoc();
    saveDec();
  };

  async function download() {
    try {
      const blob = await getBlob(`/resume-revisions/${r.id}/docx`);
      const u = URL.createObjectURL(blob);
      const a = h("a", { href: u, download: `${res.name || "履歷"}_新版履歷.docx` });
      document.body.append(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(u), 10000);
    } catch (e) { errorToast(e); }
  }
  const warnFirst = (fn) => () => {
    if (r.placeholders) toast(`還有 ${r.placeholders} 個 [待補] 沒填，記得補上再投遞`);
    fn();
  };
  async function saveAs(primary) {
    try {
      const x = await post(`/resume-revisions/${r.id}/save`, { set_primary: primary });
      setR(x); r = x;
      await loadResumes().catch(() => {});
      toast(primary ? "已存成新履歷，並設為主要履歷" : "已存成新履歷");
      drawHead();
    } catch (e) { errorToast(e); }
  }

  function drawHead() {
    if (!ctx.isCurrent()) return;
    const saved = r.saved_resume;
    replace(headBox,
      h("div", { class: "rv-res-h" },
        h("h1", null, "新版履歷"),
        h("p", { class: "muted" }, res.summary),
        h("div", { class: "rv-legend" },
          counts.new ? h("span", { class: "rv-tag new" }, `新增 ${counts.new} 條`) : null,
          counts.edit ? h("span", { class: "rv-tag edit" }, `改寫 ${counts.edit} 條`) : null,
          r.jobs.length ? h("span", { class: "faint" }, `依職缺：${r.jobs.map((j) => j.company_name).join("、")}`) : h("span", { class: "faint" }, "整體改善")),
        r.placeholders ? h("p", { class: "rv-warn" }, icon("pencil", 14), `還有 ${r.placeholders} 個 [待補]：點那一條右邊的編輯，填上實際的數字或內容。`) : null,
        h("div", { class: "row rv-acts" },
          h("button", { class: "btn out sm", type: "button", onclick: warnFirst(download) }, icon("download", 15), "下載 Word"),
          h("button", { class: "btn out sm", type: "button", onclick: warnFirst(() => copyText(plainText(finalDoc(res, dec)), "已複製新版履歷")) }, icon("copy", 15), "複製文字"),
          saved ? h("span", { class: "faint" }, icon("check", 14), ` 已存成「${saved.filename}」`)
            : h("button", { class: "btn out sm", type: "button", onclick: (e) => openMenu(e.currentTarget, [
              { label: "存成新履歷", sub: "原本那份不動", v: "plain" }, { label: "存成新履歷並設為主要", sub: "之後的面試、題庫都用這份", v: "primary" }],
            (v) => saveAs(v === "primary")) }, icon("file", 15), "存成新履歷", icon("chev", 14)),
          saved ? h("a", { class: "btn pri sm", href: "#new", onclick: () => { S.resumeSel = saved.id; } }, icon("mic", 15), "用新履歷練一場面試") : null)));
  }

  function line(ln) {
    const d = dec[ln.id] || {};
    const off = d.accept === false;
    const shown = off ? (ln.change === "edit" ? ln.original : ln.text) : d.text || ln.text;
    if (editing === ln.id) {
      const ta = h("textarea", { class: "inp", rows: 2, maxlength: 1000 }, shown);
      setTimeout(() => { ta.focus(); const m = shown.search(PLACEHOLDER); if (m >= 0) ta.setSelectionRange(m, m + shown.match(PLACEHOLDER)[0].length); });
      return h("li", { class: "rv-line editing" }, ta, h("div", { class: "row", style: "margin-top:6px" },
        h("button", { class: "btn pri sm", type: "button", onclick: () => setDec(ln.id, { accept: true, text: ta.value.trim() === ln.text ? undefined : ta.value.trim() }) }, "儲存"),
        h("button", { class: "linkb", type: "button", onclick: () => { editing = null; drawDoc(); } }, "取消")));
    }
    const marked = [];
    let last = 0;
    for (const m of shown.matchAll(PLACEHOLDER)) {
      marked.push(shown.slice(last, m.index), h("mark", { class: "rv-ph" }, m[0]));
      last = m.index + m[0].length;
    }
    marked.push(shown.slice(last));
    const changed = ln.change !== "keep";
    return h("li", { class: `rv-line${changed && !off ? ` ${ln.change}` : ""}${off ? " off" : ""}` },
      h("div", { class: "rv-lt" },
        h("span", { class: "tx" }, marked),
        h("span", { class: "rv-lacts" },
          changed ? h("button", { class: "linkb", type: "button", onclick: () => setDec(ln.id, { accept: off, text: undefined }) }, off ? "改回建議" : "還原") : null,
          off && ln.change === "new" ? null : h("button", { class: "ib sm", type: "button", "aria-label": "編輯這一條", onclick: () => { editing = ln.id; drawDoc(); } }, icon("pencil", 14)))),
      changed && !off ? h("div", { class: "rv-why" },
        h("span", { class: `rv-tag ${ln.change}` }, TAG[ln.change]), ln.reason ? h("span", null, ln.reason) : null,
        ln.change === "edit" ? h("details", null, h("summary", null, "原文"), h("p", null, ln.original)) : null) : null,
      off && ln.change === "new" ? h("div", { class: "rv-why" }, h("span", { class: "faint" }, "已拿掉這條新增的內容")) : null);
  }

  function drawDoc() {
    replace(docBox,
      h("div", { class: "rv-name" }, res.name || "你的名字"),
      res.headline ? h("div", { class: "rv-sub" }, res.headline) : null,
      res.contact ? h("div", { class: "rv-sub faint" }, res.contact) : null,
      res.sections.map((s) => h("section", null, h("h3", null, s.title),
        s.entries.map((e) => h("div", { class: "rv-entry" },
          e.title || e.meta ? h("div", { class: "rv-eh" }, e.title ? h("b", null, e.title) : null, e.meta ? h("span", { class: "faint" }, e.meta) : null) : null,
          h("ul", null, e.lines.map(line)))))));
  }

  const declined = (r.cards || []).some((c, i) => c.kind === "gap" && /沒有|學過/.test(r.answers[i]?.choice || ""));
  replace(wrap, headBox, docBox,
    res.tips.length || declined ? h("section", { class: "rv-tips" }, h("h2", null, "還可以再補強"),
      h("ul", null, res.tips.map((t) => h("li", null, t))),
      declined ? h("p", { class: "faint" }, "有些職缺要求你還沒有實戰經驗，履歷不會寫成你會。", h("a", { href: "#career" }, "去能力落差分析排準備計畫"), "。") : null) : null,
    h("div", { class: "row", style: "justify-content:center;margin-top:32px" }, h("a", { class: "linkb", href: "#revise" }, icon("left", 14), "回到履歷修改")));
  drawHead();
  drawDoc();
}
