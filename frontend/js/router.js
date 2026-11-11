// Hash 路由：#new、#chat/{session}、#report[/{session}]、#questions[/{target}]、#prep[/{target}]、#career、#targets[/{target}]、#jobs、#admin
import { $, clear } from "./dom.js";
import * as admin from "./pages/admin.js";
import * as career from "./pages/career.js";
import * as chat from "./pages/chat.js";
import * as jobs from "./pages/jobs.js";
import * as newPage from "./pages/new.js";
import * as prep from "./pages/prep.js";
import * as questions from "./pages/questions.js";
import * as report from "./pages/report.js";
import * as targets from "./pages/targets.js";
import { closeDrawer, renderSide } from "./shell.js";
import { feature, S } from "./store.js";
import { closeMenu, closeModal } from "./ui.js";

const PAGES = { new: newPage, chat, career, report, questions, prep, targets, jobs, admin };
let cleanup = null;
let seq = 0;

export function startRouter() {
  window.addEventListener("hashchange", route);
}

/** 重新繪製目前頁面（資料更新後呼叫）。 */
export function rerender() { return route({ keepScroll: true }); }

export async function route(opts = {}) {
  const [name, arg] = (location.hash || "#new").slice(1).split("/");
  // 職缺搜尋關閉時，直接輸入 #jobs 也導回新面試
  // 管理後台只對名單內、用 Google 登入的帳號顯示（後端每個後台 API 也會再檢查）
  const off = (name === "jobs" && !feature("job_search")) || (name === "admin" && !S.me?.admin_console);
  if (off) history.replaceState(null, "", "#new");
  const next = PAGES[name] && !off ? name : "new";
  const my = ++seq;
  if (cleanup) { try { await cleanup({ next }); } catch { /* ignore */ } cleanup = null; }
  if (my !== seq) return;
  const view = $("#view");
  const scroll = view.scrollTop;
  closeMenu();
  if (!opts.keepScroll) { closeModal(); closeDrawer(); }
  S.route = next;
  $("#dock").hidden = true;
  renderSide();
  clear(view);
  const page = PAGES[next];
  const res = await page.render(view, arg || null, { isCurrent: () => my === seq });
  if (my !== seq) { if (typeof res === "function") res({ next: S.route }); return; }
  cleanup = typeof res === "function" ? res : null;
  view.scrollTop = opts.keepScroll ? scroll : (next === "chat" ? view.scrollHeight : 0);
  renderSide();
}
