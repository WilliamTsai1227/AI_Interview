// Hash 路由：#new、#chat/{session}、#report[/{session}]、#targets[/{target}[/reports]]、#questions/{target}、#prep/{target}、#revise[/{revision}]、#career、#jobs、#admin
// 職缺是導覽的中心：#questions/{target}（題庫）、#prep/{target}（面試建議）、#targets/{target}/reports（報告）都是職缺頁的分頁
import { $, clear } from "./dom.js";
import * as admin from "./pages/admin.js";
import * as career from "./pages/career.js";
import * as chat from "./pages/chat.js";
import * as jobs from "./pages/jobs.js";
import * as newPage from "./pages/new.js";
import * as prep from "./pages/prep.js";
import * as questions from "./pages/questions.js";
import * as report from "./pages/report.js";
import * as revise from "./pages/revise.js";
import * as targets from "./pages/targets.js";
import { stopTour } from "./components/tour.js";
import { closeDrawer, renderSide } from "./shell.js";
import { feature, S } from "./store.js";
import { closeMenu, closeModal, confirmBox } from "./ui.js";

const PAGES = { new: newPage, chat, career, revise, report, questions, prep, targets, jobs, admin };
let cleanup = null;
let seq = 0;
let shown = location.hash;       // 目前畫面對應的網址（離開被取消時改回這個）
let leaveGuard = null;           // 頁面有未儲存的修改時回傳 true

/** 頁面編輯中呼叫：fn() 回傳 true 代表有未儲存的修改，離開前會先確認。換頁時自動清除。 */
export function setLeaveGuard(fn) { leaveGuard = fn; }

export function startRouter() {
  window.addEventListener("hashchange", route);
  window.addEventListener("beforeunload", (e) => { if (leaveGuard?.()) { e.preventDefault(); e.returnValue = ""; } });
}

/** 重新繪製目前頁面（資料更新後呼叫）。 */
export function rerender() { return route({ keepScroll: true }); }

/** 面試畫面：收起側欄、改成「人像＋右側欄」版面；離開時把側欄恢復成進來前的樣子。 */
function immersive(on) {
  const app = $("#app");
  $("#view").classList.toggle("stage-view", on);
  if (on === app.classList.contains("immersive")) return;
  if (on) {
    app.dataset.collapsed = app.classList.contains("collapsed") ? "1" : "";
    app.classList.add("immersive", "collapsed");
  } else {
    app.classList.remove("immersive");
    app.classList.toggle("collapsed", app.dataset.collapsed === "1");
  }
}

export async function route(opts = {}) {
  let [name, arg, sub] = (location.hash || "#new").slice(1).split("/");
  // 舊的「先選一個職缺」列表頁（題庫生成、面試建議）：改到職缺列表
  if ((name === "questions" || name === "prep") && !arg) { history.replaceState(null, "", "#targets"); [name, arg, sub] = ["targets", undefined, undefined]; }
  // 職缺搜尋關閉時，直接輸入 #jobs 也導回新面試
  // 管理後台只對名單內、用 Google 登入的帳號顯示（後端每個後台 API 也會再檢查）
  const off = (name === "jobs" && !feature("job_search")) || (name === "admin" && !S.me?.admin_console);
  if (off) history.replaceState(null, "", "#new");
  const next = PAGES[name] && !off ? name : "new";
  if (!opts.keepScroll && leaveGuard?.() && location.hash !== shown) {
    const ok = await confirmBox({ title: "放棄還沒儲存的修改？", text: "你正在編輯的內容還沒儲存，離開後會不見。",
      ok: "放棄修改並離開", cancel: "繼續編輯", danger: true });
    if (!ok) { history.replaceState(null, "", shown || "#new"); return; }
  }
  leaveGuard = null;
  const my = ++seq;
  if (cleanup) { try { await cleanup({ next }); } catch { /* ignore */ } cleanup = null; }
  if (my !== seq) return;
  const view = $("#view");
  const scroll = view.scrollTop;
  closeMenu();
  if (!opts.keepScroll) { closeModal(); closeDrawer(); stopTour(); }
  S.route = next;
  S.routeArg = arg || null;
  shown = location.hash;
  const dock = $("#dock");
  dock.hidden = true;
  if (dock.parentElement !== view.parentElement) view.parentElement.append(dock);    // 面試畫面會把操作列移進人像欄
  immersive(next === "chat");
  renderSide();
  clear(view);
  const page = PAGES[next];
  const res = await page.render(view, arg || null, { isCurrent: () => my === seq, sub: sub || null });
  if (my !== seq) { if (typeof res === "function") res({ next: S.route }); return; }
  cleanup = typeof res === "function" ? res : null;
  view.scrollTop = opts.keepScroll ? scroll : (next === "chat" ? view.scrollHeight : 0);
  renderSide();
}
