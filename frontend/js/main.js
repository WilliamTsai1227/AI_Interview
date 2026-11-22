// 進入點：恢復登入 → 載入 /me → 側欄與路由。
import { post, setLoggedOutHandler, setToken, tryRestoreSession } from "./api.js";
import { openSearch } from "./components/search.js";
import { openSettings } from "./components/settings.js";
import { resetTours, startTour } from "./components/tour.js";
import { WELCOME } from "./components/tours.js";
import { $, icon, replace } from "./dom.js";
import { renderLogin } from "./pages/login.js";
import { route, startRouter } from "./router.js";
import { loadMe, loadTargets, S } from "./store.js";
import { applyTheme, closeDrawer, isDark, mobile, setTheme } from "./shell.js";
import { closeMenu, closeModal, openMenu, toast } from "./ui.js";

async function logout() {
  try { await post("/auth/logout"); } catch { /* ignore */ }
  setToken(null);
  location.hash = "";
  location.reload();
}

function bindShell() {
  replace($("#brand"), icon("brand"), "AI Interview");
  replace($("#sSearch"), icon("search"));
  replace($("#sClose"), icon("side"));
  replace($("#hOpen"), icon("side"));
  replace($("#hNew"), icon("edit"));
  $("#sClose").onclick = () => (mobile() ? closeDrawer() : $("#app").classList.add("collapsed"));
  $("#hOpen").onclick = () => (mobile() ? $("#app").classList.add("open") : $("#app").classList.remove("collapsed"));
  $("#backdrop").onclick = closeDrawer;
  $("#sSearch").onclick = () => { closeDrawer(); openSearch(); };
  $("#themeBtn").onclick = () => setTheme(isDark() ? "light" : "dark");
  // 鍵盤使用者：不用每頁先 Tab 過整個側欄（網址用 # 切頁，所以用按鈕而不是 <a href="#main">）
  $("#skipBtn").onclick = () => $("#view").focus();
  $("#meBtn").onclick = (e) => openMenu(e.currentTarget, [
    { header: true, label: S.me.user.display_name, sub: S.me.user.email },
    { icon: "file", label: "履歷", v: "resume" },
    { icon: "gear", label: "設定", v: "general" },
    { icon: "bulb", label: "使用教學", v: "tour" },
    { sep: true },
    { icon: isDark() ? "sun" : "moon", label: isDark() ? "切換為淺色" : "切換為深色", v: "theme" },
    { icon: "logout", label: "登出", v: "logout" },
  ], (v) => {
    closeDrawer();
    if (v === "theme") setTheme(isDark() ? "light" : "dark");
    else if (v === "tour") replayTour();
    else if (v === "logout") logout();
    else openSettings(v);
  }, "up");
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { closeMenu(); closeModal(); }
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); openSearch(); }
  });
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", applyTheme);
}

// 帳號選單「使用教學」：清掉看過的紀錄，回到新面試頁從頭播放（其他畫面的導覽之後第一次進去也會再出現）
function replayTour() {
  resetTours();
  if ((location.hash || "#new") === "#new") startTour("welcome", WELCOME);
  else location.hash = "#new";
}

export async function boot() {
  applyTheme();
  setLoggedOutHandler(() => { toast("登入已過期，請重新登入"); setTimeout(() => location.reload(), 800); });
  const s = await tryRestoreSession();
  if (!s) {
    $("#app").hidden = true;
    $("#login").hidden = false;
    document.body.classList.add("login-mode");
    await renderLogin($("#login"), async () => { await enterApp(); });
    return;
  }
  await enterApp();
}

let shellBound = false;
async function enterApp() {
  $("#login").hidden = true;
  document.body.classList.remove("login-mode");
  $("#app").hidden = false;
  if (!shellBound) { bindShell(); shellBound = true; }
  await Promise.all([loadMe(), loadTargets()]);
  applyTheme();
  startRouter();
  route();
  mcpReturn();
}

// MCP 連接器授權完成後，後端導回 /?mcp_connected=… 或 /?mcp_error=…；顯示一次後從網址移除
const MCP_ERRORS = { cancelled: "已取消連接", state: "連結已失效，請再試一次", failed: "連接失敗，請再試一次" };
function mcpReturn() {
  const q = new URLSearchParams(location.search);
  if (!q.has("mcp_connected") && !q.has("mcp_error")) return;
  history.replaceState(null, "", location.pathname + location.hash);
  toast(q.has("mcp_connected") ? "已連接 Notion" : (MCP_ERRORS[q.get("mcp_error")] || MCP_ERRORS.failed));
  openSettings("mcp");
}

boot();
