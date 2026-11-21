// 外框：主題、側欄。給各頁面共用（避免頁面與 main.js 循環匯入）。
import { $, append, clear, h, icon, replace } from "./dom.js";
import { feature, S } from "./store.js";
import { openMenu } from "./ui.js";

// ---------- 主題 ----------
export const isDark = () => (S.theme ? S.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches);
export function applyTheme() {
  if (S.theme) document.documentElement.setAttribute("data-theme", S.theme);
  else document.documentElement.removeAttribute("data-theme");
  replace($("#themeBtn"), icon(isDark() ? "sun" : "moon"));
  // 手機網址列顏色跟著頁面背景（手動切換主題時 meta 的 media 條件不會跟著變）
  const bg = isDark() ? "#212121" : "#ffffff";
  document.querySelectorAll('meta[name="theme-color"]').forEach((m) => m.setAttribute("content", bg));
}
export function setTheme(t) {
  S.theme = t;
  try { t ? localStorage.setItem("ai-theme", t) : localStorage.removeItem("ai-theme"); } catch { /* ignore */ }
  applyTheme();
}

// ---------- 側欄 ----------
// 以職缺為中心：新面試 → 職缺（直接列出，依最近練習排序）→ 面試報告、能力落差分析
const JOB_ROUTES = new Set(["targets", "questions", "prep"]);
const MAX_JOBS = 6;
export const mobile = () => matchMedia("(max-width:768px)").matches;
export const closeDrawer = () => $("#app").classList.remove("open");

const navItem = (id, label, ic, on) =>
  h("a", { class: `nav-i${on ? " on" : ""}`, href: `#${id}`, "aria-current": on ? "page" : null, onclick: closeDrawer }, icon(ic), h("span", { class: "t" }, label));

function recentFirst(tg) {
  const at = (t) => t.last_report?.ended_at || t.created_at || "";
  return [...tg].sort((a, b) => (at(b) > at(a) ? 1 : at(b) < at(a) ? -1 : 0));
}

export function renderSide() {
  const list = $("#sideList");
  clear(list);
  const act = S.me?.active_interview;
  if (act && S.route !== "chat") {
    append(list, [h("a", { class: "nav-live", href: `#chat/${act.id}`, onclick: closeDrawer },
      h("span", { class: "live-dot" }),
      h("span", { class: "t" }, `${act.status === "paused" ? "面試暫停中" : "面試進行中"}・${act.voice_mode === "live" ? "Live 對談" : `第 ${act.current_main_no}/${act.total_main} 題`}`),
      h("span", { class: "faint" }, "繼續"))]);
  }
  list.append(navItem("new", "新面試", "edit", S.route === "new" || S.route === "chat"));

  // 職缺
  const tg = recentFirst(S.targets || []);
  const inJob = JOB_ROUTES.has(S.route) ? S.routeArg : null;
  const add = h("button", { class: "ib sm", type: "button", "aria-label": "新增職缺", onclick: (e) =>
    import("./components/jd.js").then(({ addTargetMenuItems, onAddTargetPick }) => openMenu(e.currentTarget, addTargetMenuItems(), (v) => { closeDrawer(); onAddTargetPick(v); })) }, icon("plus"));
  list.append(h("div", { class: "side-h side-grp" }, h("a", { href: "#targets", onclick: closeDrawer }, "職缺"), add));
  if (!tg.length) list.append(h("button", { class: "nav-i nav-empty", type: "button", onclick: () => { closeDrawer(); import("./components/jd.js").then((m) => m.openJd()); } },
    icon("plus"), h("span", { class: "t" }, "貼上第一個職缺")));
  for (const t of tg.slice(0, MAX_JOBS)) {
    const on = inJob === t.id;
    list.append(h("a", { class: `nav-i nav-job${on ? " on" : ""}`, href: `#targets/${t.id}`, "aria-current": on ? "page" : null, onclick: closeDrawer,
      title: `${t.job.company_name}・${t.job.title}` },
      h("span", { class: "jl", "aria-hidden": "true" }, (t.job.company_name || "?").slice(0, 1).toUpperCase()),
      h("span", { class: "t" }, h("span", null, t.job.company_name), h("small", null, t.job.title))));
  }
  if (tg.length > MAX_JOBS) list.append(navItem("targets", `全部職缺（${tg.length}）`, "target", S.route === "targets" && !S.routeArg));

  list.append(h("div", { class: "side-h", "aria-hidden": "true" }));
  list.append(navItem("report", "面試報告", "report", S.route === "report"));
  list.append(navItem("career", "能力落差分析", "brief", S.route === "career"));
  if (feature("job_search")) list.append(navItem("jobs", "職缺搜尋", "search", S.route === "jobs"));
  if (S.me?.admin_console) list.append(navItem("admin", "管理後台", "lock", S.route === "admin"));
  const u = S.me?.user;
  replace($("#meBtn"), h("span", { class: "av" }, (u?.display_name || "?").slice(-1)),
    h("span", null, h("b", null, u?.display_name || ""), h("small", null, S.me?.membership?.plan_name || (u?.plan === "pro" ? "Plus" : "Free"))));
}

