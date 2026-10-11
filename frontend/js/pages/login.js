// 登入／註冊：註冊後直接進入「新面試」，不要求先填個人資料。
import { post, setToken } from "../api.js";
import { h, icon, replace } from "../dom.js";

const AUTH_ERRORS = {
  cancelled: "已取消 Google 登入",
  state: "登入逾時或連結已失效，請再試一次",
  unverified: "這個 Google 帳號的 Email 尚未驗證，無法登入",
  failed: "Google 登入失敗，請再試一次",
  disabled: "這個帳號已停用",
};

export async function renderLogin(root, onDone) {
  let mode = "login";
  // Google 登入失敗時後端導回 /?auth_error=…；顯示一次後從網址移除
  const params = new URLSearchParams(location.search);
  let firstError = AUTH_ERRORS[params.get("auth_error")] || "";
  if (params.has("auth_error")) history.replaceState(null, "", location.pathname + location.hash);
  let google = false;
  try { google = (await fetch("/api/v1/auth/providers").then((r) => r.json())).google; } catch { /* 只顯示密碼登入 */ }
  const draw = () => {
    const err = h("div", { class: "form-err", role: "alert" }, firstError);
    firstError = "";
    const name = h("input", { class: "inp", type: "text", placeholder: "怎麼稱呼你（選填）", autocomplete: "name", "aria-label": "名稱" });
    const email = h("input", { class: "inp", type: "email", placeholder: "Email", autocomplete: "email", required: true, "aria-label": "Email" });
    const pw = h("input", { class: "inp", type: "password", placeholder: mode === "login" ? "密碼" : "密碼（至少 8 碼）",
      autocomplete: mode === "login" ? "current-password" : "new-password", required: true, "aria-label": "密碼" });
    const btn = h("button", { class: "btn pri", type: "submit" }, mode === "login" ? "登入" : "建立帳號");
    const form = h("form", { class: "login-card", novalidate: true, onsubmit: async (e) => {
      e.preventDefault();
      err.textContent = "";
      if (!email.value.trim() || !pw.value) { err.textContent = "請輸入 Email 和密碼"; return; }
      btn.disabled = true;
      btn.textContent = "請稍候⋯";
      try {
        const body = mode === "login" ? { email: email.value.trim(), password: pw.value }
          : { email: email.value.trim(), password: pw.value, display_name: name.value.trim() };
        const r = await post(mode === "login" ? "/auth/login" : "/auth/register", body);
        setToken(r.access_token);
        await onDone();
      } catch (ex) {
        err.textContent = ex.code === "VALIDATION_ERROR" && ex.details?.fields?.email ? "Email 格式不正確" : ex.message;
        if (ex.code === "USE_GOOGLE" && google) pw.value = "";
        btn.disabled = false;
        btn.textContent = mode === "login" ? "登入" : "建立帳號";
      }
    } },
      h("div", { class: "brand" }, icon("brand"), "AI Interview"),
      h("h1", null, mode === "login" ? "歡迎回來" : "建立帳號"),
      h("p", { class: "sub" }, "針對你的目標職缺，一題一題練習語音面試。"),
      google ? [h("a", { class: "btn google", href: "/api/v1/auth/google/start" }, icon("google"), "使用 Google 繼續"),
        h("div", { class: "or" }, "或使用 Email")] : null,
      mode === "register" ? name : null, email, pw, err, btn,
      h("div", { class: "switch-auth" }, mode === "login" ? "還沒有帳號？" : "已經有帳號？",
        h("button", { type: "button", onclick: () => { mode = mode === "login" ? "register" : "login"; draw(); } },
          mode === "login" ? "註冊" : "登入")));
    replace(root, h("div", { class: "login" }, form));
    (mode === "register" ? name : email).focus();
  };
  draw();
}
