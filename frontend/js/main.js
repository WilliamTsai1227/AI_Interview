// 進入點：恢復登入 → 載入 /me → 側欄與路由。
import { post, setLoggedOutHandler, setToken, tryRestoreSession } from "./api.js";
import { openSearch } from "./components/search.js";
import { openSettings } from "./components/settings.js";
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
  $("#meBtn").onclick = (e) => openMenu(e.currentTarget, [
    { header: true, label: S.me.user.display_name, sub: S.me.user.email },
    { icon: "file", label: "履歷", v: "resume" },
    { icon: "gear", label: "設定", v: "general" },
    { sep: true },
    { icon: isDark() ? "sun" : "moon", label: isDark() ? "切換為淺色" : "切換為深色", v: "theme" },
    { icon: "logout", label: "登出", v: "logout" },
  ], (v) => {
    closeDrawer();
    if (v === "theme") setTheme(isDark() ? "light" : "dark");
    else if (v === "logout") logout();
    else openSettings(v);
  }, "up");
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { closeMenu(); closeModal(); }
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); openSearch(); }
  });
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", applyTheme);
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
}

boot();
