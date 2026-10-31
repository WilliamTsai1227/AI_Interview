// 外框：主題、側欄。給各頁面共用（避免頁面與 main.js 循環匯入）。
import { $, append, clear, h, icon, replace } from "./dom.js";
import { feature, S } from "./store.js";

// ---------- 主題 ----------
export const isDark = () => (S.theme ? S.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches);
export function applyTheme() {
  if (S.theme) document.documentElement.setAttribute("data-theme", S.theme);
  else document.documentElement.removeAttribute("data-theme");
  replace($("#themeBtn"), icon(isDark() ? "sun" : "moon"));
}
export function setTheme(t) {
  S.theme = t;
  try { t ? localStorage.setItem("ai-theme", t) : localStorage.removeItem("ai-theme"); } catch { /* ignore */ }
  applyTheme();
}

// ---------- 側欄 ----------
const NAV = [["new", "新面試", "edit"], ["questions", "題庫生成", "book"], ["jobs", "職缺搜尋", "search"],
  ["targets", "目標職缺", "target"], ["prep", "面試建議", "bulb"], ["career", "職涯規劃", "brief"], ["report", "面試報告", "report"]];
export const mobile = () => matchMedia("(max-width:768px)").matches;
export const closeDrawer = () => $("#app").classList.remove("open");

export function renderSide() {
  const list = $("#sideList");
  clear(list);
  const act = S.me?.active_interview;
  if (act && S.route !== "chat") {
    append(list, [h("a", { class: "nav-live", href: `#chat/${act.id}`, onclick: closeDrawer },
      h("span", { class: "live-dot" }),
      h("span", { class: "t" }, `${act.status === "paused" ? "面試暫停中" : "面試進行中"}・第 ${act.current_main_no}/${act.total_main} 題`),
      h("span", { class: "faint" }, "繼續"))]);
  }
  for (const [id, label, ic] of NAV.filter(([id]) => id !== "jobs" || feature("job_search"))) {
    const on = S.route === id || (S.route === "chat" && id === "new");
    list.append(h("a", { class: `nav-i${on ? " on" : ""}`, href: `#${id}`, onclick: closeDrawer }, icon(ic), h("span", { class: "t" }, label)));
  }
  const u = S.me?.user;
  replace($("#meBtn"), h("span", { class: "av" }, (u?.display_name || "?").slice(-1)),
    h("span", null, h("b", null, u?.display_name || ""), h("small", null, u?.plan === "pro" ? "Plus" : "Free")));
}

