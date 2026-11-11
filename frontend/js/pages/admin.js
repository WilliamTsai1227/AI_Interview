// 管理後台（#admin）：會員方案與 token 額度。
// 進入條件（後端每個 API 都會檢查）：帳號在 ADMIN_GOOGLE_EMAILS 名單、這次用 Google 登入、輸入後台帳號密碼。
// 後台工作階段只存在 sessionStorage（關掉分頁就失效），到期後要重新輸入密碼。
import { api } from "../api.js";
import { dots, h, icon, replace } from "../dom.js";
import { fmtDate, S } from "../store.js";
import { closeModal, errorToast, openModal, seg, setHead, toast } from "../ui.js";

const KEY = "ai-admin-session";
const fmtN = (n) => (n == null ? "不限" : Number(n).toLocaleString());
const pct = (used, lim) => (lim == null ? null : lim === 0 ? 100 : Math.min(999, Math.round((used / lim) * 100)));

function loadSession() {
  try {
    const s = JSON.parse(sessionStorage.getItem(KEY) || "null");
    return s && new Date(s.expires_at) > new Date() ? s : null;
  } catch { return null; }
}
function saveSession(s) { try { s ? sessionStorage.setItem(KEY, JSON.stringify(s)) : sessionStorage.removeItem(KEY); } catch { /* ignore */ } }

let redraw = () => {};

/** 後台 API：帶後台工作階段；工作階段失效 → 回到輸入密碼畫面。 */
async function adm(path, opts = {}) {
  const s = loadSession();
  try {
    return await api(`/admin${path}`, { ...opts, headers: { ...(opts.headers || {}), "X-Admin-Session": s?.session || "" } });
  } catch (e) {
    if (e.code === "ADMIN_SESSION_REQUIRED") { saveSession(null); closeModal(); toast("後台工作階段已過期，請重新輸入密碼"); redraw(); }
    throw e;
  }
}

export async function render(view, _arg, ctx) {
  setHead("lock", "管理後台");
  const wrap = h("div", { class: "wrap wide doc" }, h("div", { class: "center-load" }, dots()));
  view.append(wrap);
  if (!S.me?.admin_console) {
    replace(wrap, h("h1", null, "管理後台"),
      h("p", { class: "muted", style: "margin-top:8px" }, "這個帳號無法使用管理後台。管理員請用指定的 Google 帳號登入。"));
    return;
  }
  const state = { tab: "members", q: "", plan: "", over: false };
  redraw = () => { if (ctx.isCurrent()) draw(); };
  const draw = () => (loadSession() ? dashboard(wrap, state, draw) : lockScreen(wrap, draw));
  draw();
  return () => { redraw = () => {}; };
}

// ---------- 輸入後台帳號密碼 ----------
function lockScreen(wrap, done) {
  const user = h("input", { class: "inp", placeholder: "後台帳號", autocomplete: "username", "aria-label": "後台帳號" });
  const pw = h("input", { class: "inp", type: "password", placeholder: "後台密碼", autocomplete: "current-password", "aria-label": "後台密碼" });
  const err = h("div", { class: "form-err" });
  const btn = h("button", { class: "btn pri", type: "submit" }, "進入後台");
  const form = h("form", { class: "adm-lock", onsubmit: async (e) => {
    e.preventDefault();
    err.textContent = "";
    btn.disabled = true;
    try {
      const r = await api("/admin/console/login", { method: "POST", body: { username: user.value, password: pw.value } });
      saveSession(r);
      done();
    } catch (ex) { err.textContent = ex.message; pw.value = ""; pw.focus(); }
    finally { btn.disabled = false; }
  } },
  h("div", { class: "adm-lock-ic" }, icon("lock", 22)),
  h("h1", null, "管理後台"),
  h("p", { class: "muted" }, `已用 Google 登入 ${S.me.user.email}。請再輸入後台帳號密碼。`),
  user, pw, err, btn);
  replace(wrap, form);
  user.focus();
}

// ---------- 主畫面 ----------
async function dashboard(wrap, state, draw) {
  const s = loadSession();
  const body = h("div", { class: "adm-body" }, h("div", { class: "center-load" }, dots()));
  replace(wrap,
    h("div", { class: "adm-top" },
      h("h1", null, "管理後台"),
      h("span", { class: "faint", style: "font-size:.84rem" }, `工作階段到 ${new Date(s.expires_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`),
      h("button", { class: "btn out sm", type: "button", onclick: () => { saveSession(null); draw(); } }, icon("lock", 14), "鎖定")),
    h("div", { style: "margin:18px 0 16px" }, seg([["members", "會員"], ["plans", "方案"]], state.tab, (t) => { state.tab = t; draw(); })),
    body);
  try {
    const plans = (await adm("/plans")).items;
    if (state.tab === "plans") plansTab(body, plans, draw);
    else await membersTab(body, plans, state);
  } catch (e) {
    if (e.code !== "ADMIN_SESSION_REQUIRED") replace(body, h("p", { class: "form-err" }, e.message));
  }
}

// ---------- 會員 ----------
async function membersTab(body, plans, state) {
  const planName = Object.fromEntries(plans.map((p) => [p.code, p.name]));
  const q = h("input", { class: "inp", value: state.q, placeholder: "搜尋 Email 或名稱", "aria-label": "搜尋會員", style: "max-width:260px" });
  const planSel = h("select", { class: "selx", "aria-label": "方案", onchange: (e) => { state.plan = e.target.value; load(); } },
    h("option", { value: "" }, "全部方案"), plans.map((p) => h("option", { value: p.code, selected: p.code === state.plan }, p.name)));
  const over = h("label", { class: "row", style: "font-size:.88rem;gap:6px" },
    h("input", { type: "checkbox", checked: state.over, onchange: (e) => { state.over = e.target.checked; load(); } }), "只看已達上限");
  let t;
  q.addEventListener("input", () => { clearTimeout(t); t = setTimeout(() => { state.q = q.value.trim(); load(); }, 300); });
  const list = h("div", { class: "tbl-w" });
  replace(body, h("div", { class: "row", style: "margin-bottom:14px;gap:12px" }, q, planSel, over), list);

  async function load() {
    replace(list, h("div", { class: "center-load" }, dots()));
    const p = new URLSearchParams({ limit: "200" });
    if (state.q) p.set("q", state.q);
    if (state.plan) p.set("plan", state.plan);
    if (state.over) p.set("over_quota", "true");
    let items;
    try { items = (await adm(`/users?${p}`)).items; } catch (e) { replace(list, h("p", { class: "form-err", style: "padding:14px" }, e.message)); return; }
    if (!items.length) { replace(list, h("p", { class: "muted", style: "padding:14px" }, "沒有符合的會員")); return; }
    replace(list, h("table", null,
      h("thead", null, h("tr", null, h("th", null, "會員"), h("th", null, "方案"), h("th", null, "本月 token"), h("th", { class: "n" }, "呼叫"), h("th", null, "到期"))),
      h("tbody", null, items.map((u) => {
        const pc = pct(u.tokens_used, u.token_limit);
        return h("tr", { class: "adm-row", tabindex: "0", onclick: () => memberModal(u.id, plans, load),
          onkeydown: (e) => { if (e.key === "Enter") memberModal(u.id, plans, load); } },
        h("td", null, h("b", null, u.display_name), u.role === "admin" ? h("span", { class: "adm-tag" }, "admin") : null,
          h("div", { class: "faint", style: "font-size:.82rem" }, u.email)),
        h("td", null, planName[u.plan] || u.plan, u.token_limit_override != null ? h("span", { class: "adm-tag" }, "自訂上限") : null),
        h("td", null, h("div", null, `${fmtN(u.tokens_used)} / ${fmtN(u.token_limit)}`),
          pc != null ? h("div", { class: `adm-meter${pc >= 100 ? " over" : pc >= 80 ? " warn" : ""}` }, h("i", { style: `width:${Math.min(100, pc)}%` })) : null),
        h("td", { class: "n" }, u.calls),
        h("td", null, u.plan_expires_at ? fmtDate(u.plan_expires_at) : "—"));
      }))));
  }
  await load();
}

async function memberModal(uid, plans, reload) {
  openModal({ title: "會員額度", body: [h("div", { class: "center-load" }, dots())] });
  let d;
  try { d = await adm(`/users/${uid}/usage`); } catch (e) { closeModal(); if (e.code !== "ADMIN_SESSION_REQUIRED") errorToast(e); return; }
  const m = d.membership;
  const planSel = h("select", { class: "selx", "aria-label": "方案" },
    plans.filter((p) => p.is_active || p.code === m.raw_plan).map((p) => h("option", { value: p.code, selected: p.code === m.raw_plan }, `${p.name}（${fmtN(p.monthly_token_limit)} tokens）`)));
  const exp = h("input", { class: "inp", type: "date", "aria-label": "方案到期日", value: m.plan_expires_at ? m.plan_expires_at.slice(0, 10) : "" });
  const ovr = h("input", { class: "inp", type: "number", min: "0", step: "1000", placeholder: "留白＝依方案", "aria-label": "自訂 token 上限",
    value: m.token_limit_overridden ? String(m.tokens_limit) : "" });
  const reason = h("input", { class: "inp", maxlength: 300, placeholder: "原因（選填，會記在異動紀錄）", "aria-label": "原因" });
  const err = h("div", { class: "form-err" });
  const save = h("button", { class: "btn pri", type: "button", onclick: async () => {
    err.textContent = "";
    const body = { plan: planSel.value, reason: reason.value.trim() || null,
      plan_expires_at: exp.value ? new Date(`${exp.value}T23:59:59`).toISOString() : null,
      token_limit_override: ovr.value === "" ? null : Number(ovr.value) };
    save.disabled = true;
    try { await adm(`/users/${uid}/membership`, { method: "PATCH", body }); toast("已更新"); closeModal(); reload(); }
    catch (e) { err.textContent = e.message; }
    finally { save.disabled = false; }
  } }, "儲存");
  const pc = pct(m.tokens_used, m.tokens_limit);
  openModal({ title: "會員額度", body: [
    h("div", { class: "adm-stats" },
      stat("生效方案", m.plan_name + (m.plan !== m.raw_plan ? "（已到期）" : "")),
      stat("本月已用", `${fmtN(m.tokens_used)}${pc != null ? `（${pc}%）` : ""}`),
      stat("上限 / 硬上限", `${fmtN(m.tokens_limit)} / ${fmtN(m.hard_limit)}`),
      stat("重置", fmtDate(m.resets_at))),
    h("div", { class: "adm-form" },
      field("方案", planSel), field("到期日（留白＝不到期）", exp), field("自訂 token 上限", ovr), field("原因", reason)),
    err,
    h("div", { class: "row", style: "justify-content:flex-end;margin-top:12px" },
      h("button", { class: "btn out", type: "button", onclick: closeModal }, "取消"), save),
    d.this_month_by_purpose.length ? [h("h3", { class: "adm-h" }, "本月各功能用量"), miniTable(["功能", "呼叫", "輸入", "輸出"],
      d.this_month_by_purpose.map((r) => [r.purpose, r.calls, fmtN(r.input_tokens), fmtN(r.output_tokens)]))] : null,
    d.history.length ? [h("h3", { class: "adm-h" }, "每月用量"), miniTable(["月份", "呼叫", "token"],
      d.history.map((r) => [r.period_start.slice(0, 7), r.call_count, fmtN(r.total_tokens)]))] : null,
    d.plan_changes.length ? [h("h3", { class: "adm-h" }, "方案異動"), miniTable(["時間", "異動", "上限", "原因"],
      d.plan_changes.map((c) => [fmtDate(c.created_at), `${c.from_plan || "—"} → ${c.to_plan}`, c.token_limit_override != null ? fmtN(c.token_limit_override) : "依方案", c.reason || ""]))] : null,
  ] });
}

// ---------- 方案 ----------
function plansTab(body, plans, draw) {
  replace(body,
    h("div", { class: "tbl-w" }, h("table", null,
      h("thead", null, h("tr", null, h("th", null, "方案"), h("th", null, "每月 token"), h("th", null, "每月面試"), h("th", null, "硬上限倍數"),
        h("th", null, "價格"), h("th", { class: "n" }, "會員"), h("th", null, ""))),
      h("tbody", null, plans.map((p) => h("tr", null,
        h("td", null, h("b", null, p.name), h("div", { class: "faint", style: "font-size:.82rem" }, p.code), !p.is_active ? h("span", { class: "adm-tag" }, "停用") : null),
        h("td", null, fmtN(p.monthly_token_limit)), h("td", null, fmtN(p.monthly_interview_limit)), h("td", null, `× ${p.hard_limit_ratio}`),
        h("td", null, p.price_twd ? `NT$${p.price_twd}` : "—"), h("td", { class: "n" }, p.members),
        h("td", null, h("button", { class: "btn out sm", type: "button", onclick: () => planModal(p, draw) }, icon("pencil", 14), "編輯"))))))),
    h("button", { class: "addrow", type: "button", style: "margin-top:12px", onclick: () => planModal(null, draw) }, icon("plus"), "新增方案"));
}

function planModal(p, draw) {
  const isNew = !p;
  const code = h("input", { class: "inp", value: p?.code || "", disabled: !isNew, placeholder: "例如 team（小寫英數與底線）", "aria-label": "方案代碼" });
  const name = h("input", { class: "inp", value: p?.name || "", maxlength: 40, "aria-label": "顯示名稱" });
  const num = (v, ph, label) => h("input", { class: "inp", type: "number", min: "0", value: v == null ? "" : String(v), placeholder: ph, "aria-label": label });
  const tok = num(p?.monthly_token_limit, "留白＝不限", "每月 token 上限");
  const itv = num(p?.monthly_interview_limit, "留白＝不限", "每月面試次數");
  const ratio = h("input", { class: "inp", type: "number", min: "1", max: "5", step: "0.05", value: String(p?.hard_limit_ratio ?? 1.2), "aria-label": "硬上限倍數" });
  const price = num(p?.price_twd ?? 0, "0", "價格");
  const sort = num(p?.sort_order ?? 50, "0", "排序");
  const active = h("input", { type: "checkbox", checked: p ? p.is_active : true });
  const err = h("div", { class: "form-err" });
  const nOrNull = (el) => (el.value === "" ? null : Number(el.value));
  const save = h("button", { class: "btn pri", type: "button", onclick: async () => {
    err.textContent = "";
    if (!code.value.trim() || !name.value.trim()) { err.textContent = "請填方案代碼與名稱"; return; }
    save.disabled = true;
    try {
      await adm(`/plans/${encodeURIComponent(code.value.trim())}`, { method: "PUT", body: {
        name: name.value.trim(), monthly_token_limit: nOrNull(tok), monthly_interview_limit: nOrNull(itv),
        hard_limit_ratio: Number(ratio.value || 1.2), price_twd: Number(price.value || 0), is_active: active.checked,
        sort_order: Number(sort.value || 0) } });
      toast("已儲存"); closeModal(); draw();
    } catch (e) { err.textContent = e.message; }
    finally { save.disabled = false; }
  } }, "儲存");
  openModal({ title: isNew ? "新增方案" : `編輯方案：${p.name}`, body: [
    h("div", { class: "adm-form" },
      field("方案代碼", code), field("顯示名稱", name), field("每月 token 上限", tok), field("每月面試次數", itv),
      field("硬上限倍數（已開始的面試可超用到上限 × 倍數）", ratio), field("價格（NT$）", price), field("排序", sort),
      h("label", { class: "row", style: "gap:6px;font-size:.9rem" }, active, "開放指派給會員")),
    !isNew ? h("p", { class: "faint", style: "font-size:.82rem;margin-top:10px" }, "改上限會立即套用到這個方案的所有會員。") : null,
    err,
    h("div", { class: "row", style: "justify-content:flex-end;margin-top:12px" },
      h("button", { class: "btn out", type: "button", onclick: closeModal }, "取消"), save),
  ] });
}

// ---------- 小元件 ----------
const field = (label, el) => h("label", { class: "adm-f" }, h("span", null, label), el);
const stat = (label, v) => h("div", { class: "adm-stat" }, h("span", null, label), h("b", null, v));
const miniTable = (cols, rows) => h("div", { class: "tbl-w" }, h("table", null,
  h("thead", null, h("tr", null, cols.map((c) => h("th", null, c)))),
  h("tbody", null, rows.map((r) => h("tr", null, r.map((c) => h("td", null, c)))))));
