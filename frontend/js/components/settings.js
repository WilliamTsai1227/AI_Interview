// 設定：一般／履歷／連接 Notion（MCP 連接器）／帳號。
import { del, get, patch, post, uploadFile } from "../api.js";
import { h, icon, replace } from "../dom.js";
import { renderSide, setTheme } from "../shell.js";
import { fmtDay, fmtSize, LANGS, loadMe, loadResumes, MODES, PERSONAS, prefs, resumesFull, S } from "../store.js";
import { closeModal, confirmBox, errorToast, openModal, toast } from "../ui.js";
import { openResumePreview } from "./resumePreview.js";

// 「求職意向」改在「能力落差分析」頁填寫，這裡不再放一個只有跳轉按鈕的分頁
const TABS = [["general", "一般", "gear"], ["resume", "履歷", "file"], ["mcp", "連接 Notion", "link"], ["account", "帳號", "user"]];
let current = "general";
let changed = null;

function select(options, value, onChange, label) {
  return h("select", { class: "selx", "aria-label": label, onchange: (e) => onChange(e.target.value) },
    options.map(([v, label]) => h("option", { value: v, selected: v === value }, label)));
}

async function savePrefs(p) {
  try {
    S.me = await patch("/me", { preferences: p });
    changed?.();
  } catch (e) { errorToast(e); }
}

function general() {
  const p = prefs();
  return [
    h("div", { class: "srow" }, h("span", null, "主題"),
      select([["light", "淺色"], ["dark", "深色"], ["system", "跟隨系統"]], S.theme || "light", (v) => setTheme(v), "主題")),
    h("div", { class: "srow" }, h("span", null, "預設面試長度"),
      select(Object.entries(MODES).map(([k, m]) => [k, `${m.name} ${m.n} 題`]), p.default_length, (v) => savePrefs({ default_length: v }), "預設面試長度")),
    h("div", { class: "srow" }, h("span", null, "預設面試語言"),
      select(Object.entries(LANGS), p.default_language, (v) => savePrefs({ default_language: v }), "預設面試語言")),
    h("div", { class: "srow" }, h("span", null, "預設面試官風格"),
      select(Object.entries(PERSONAS).map(([k, x]) => [k, x.name]), p.default_persona, (v) => savePrefs({ default_persona: v }), "預設面試官風格")),
    h("div", { class: "srow" }, h("span", null, "Ava 說話速度"),
      select([["1", "一般（最自然）"], ["1.15", "稍快"], ["1.3", "快"]], String(p.voice_speed ?? 1), (v) => savePrefs({ voice_speed: Number(v) }), "Ava 說話速度")),
    h("div", { class: "srow" }, h("span", null, "回答錄音保留"), h("span", { class: "sv" }, "180 天（逐字稿與評分會保留）")),
  ];
}

const PARSE = { pending: "等待解析", parsing: "Ava 正在解析", parsed: "AI 已解析", failed: "解析失敗" };

async function resume(body, rerender) {
  replace(body, h("div", { class: "center-load" }, "載入中⋯"));
  const list = await loadResumes();
  const primary = list.find((r) => r.is_primary);
  let detail = null;
  if (primary && primary.parse_status === "parsed") {
    try { detail = await get(`/resumes/${primary.id}`); } catch { /* ignore */ }
  }
  const file = h("input", { type: "file", id: "cvFile", accept: ".pdf,.docx", hidden: true });
  const drop = h("label", { class: "drop", for: "cvFile" }, icon("upload"),
    h("span", { style: "font-weight:500" }, "拖曳檔案到這裡，或點擊上傳"), h("span", { class: "faint" }, "PDF、Word（.docx），10 MB 以內"), file);
  const take = async (f) => {
    if (!f) return;
    if (f.size > 10 * 1024 * 1024) { toast("檔案超過 10 MB"); return; }
    replace(drop, h("span", { style: "font-weight:500" }, `正在上傳 ${f.name}⋯`));
    try {
      const uploadId = await uploadFile("resume", f, { filename: f.name });
      const r = await post("/resumes", { upload_id: uploadId });
      toast(r.parse_status === "parsed" ? "這份履歷之前上傳過了" : "履歷已上傳，Ava 正在解析");
      await loadMe();
      renderSide();
      changed?.();
      rerender();
      pollParse(r.id, rerender);
    } catch (e) { errorToast(e); rerender(); }
  };
  file.onchange = () => take(file.files[0]);
  for (const ev of ["dragenter", "dragover"]) drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); });
  for (const ev of ["dragleave", "drop"]) drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); });
  drop.addEventListener("drop", (e) => take(e.dataTransfer.files[0]));

  const preview = (r) => openResumePreview(r, { onClose: () => setTimeout(() => openSettings("resume")) });
  const rows = list.map((r) => h("div", { class: "file" }, h("span", { class: "fi" }, icon("file")),
    h("button", { type: "button", class: "file-open", title: "預覽履歷", onclick: () => preview(r) },
      h("span", { style: "font-weight:500;display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" }, r.filename),
      h("span", { class: "faint" }, `${fmtSize(r.size_bytes)}・${fmtDay(r.created_at)}${r.is_primary ? "・主要履歷" : ""}・${PARSE[r.parse_status]}`)),
    h("button", { class: "ib sm", type: "button", "aria-label": "預覽履歷", title: "預覽", onclick: () => preview(r) }, icon("eye")),
    r.is_primary ? null : h("button", { class: "btn ghost sm", type: "button", onclick: async () => {
      try { await patch(`/resumes/${r.id}`, { is_primary: true }); await loadMe(); toast("已設為主要履歷"); changed?.(); rerender(); } catch (e) { errorToast(e); }
    } }, "設為主要"),
    h("button", { class: "ib sm", type: "button", "aria-label": "刪除履歷", onclick: async () => {
      if (!(await confirmBox({ title: "刪除這份履歷？", text: "原始檔會刪除；已完成的面試報告不受影響。", ok: "刪除", danger: true }))) { openSettings("resume"); return; }
      try { await del(`/resumes/${r.id}`); await loadMe(); toast("已刪除"); changed?.(); } catch (e) { errorToast(e); }
      openSettings("resume");
    } }, icon("trash"))));

  const p = detail?.parsed, a = detail?.analysis;
  replace(body,
    h("p", { class: "faint", style: "padding-top:12px" },
      `Ava 依履歷內容出題和評分，不需要另外填寫經歷或技能。最多保留 ${S.resumeMax || 3} 份（目前 ${list.length} 份）。`),
    h("div", { class: "srow col", style: "border:0" }, rows,
      resumesFull() ? h("div", { class: "drop full" }, h("span", { style: "font-weight:500" }, `已經有 ${list.length} 份履歷`),
        h("span", { class: "faint" }, "刪除一份不用的，才能再上傳新的")) : drop),
    primary && primary.parse_status === "failed" ? h("p", { class: "form-err" }, detail?.parse_error || "解析失敗，請換成可以選取文字的 PDF 再上傳") : null,
    p ? h("div", { class: "doc" }, h("div", { class: "sub-h" }, "Ava 從履歷讀到的"),
      h("div", { class: "row", style: "margin-top:8px" }, (p.skills || []).map((s) => h("span", { class: "tagx" }, s))),
      (p.experiences || []).length ? h("ul", { style: "margin-top:10px" }, p.experiences.map((e) =>
        h("li", null, `${e.title}・${e.org}（${e.start || "?"} – ${e.end || "現在"}）`))) : (p.summary ? h("p", { class: "muted", style: "margin-top:10px" }, p.summary) : null),
      a ? h("div", { class: "grid2", style: "margin-top:4px" },
        h("div", null, h("div", { class: "sub-h" }, "履歷亮點"), h("ul", { class: "ticks" }, (a.highlights || []).map((x) => h("li", null, x)))),
        h("div", null, h("div", { class: "sub-h" }, "可以更好"), h("ul", { class: "ticks warn" }, (a.improvements || []).map((x) => h("li", null, x))))) : null,
      h("p", { class: "faint", style: "margin-top:12px" }, "讀錯了嗎？直接修改履歷檔案後重新上傳就好。")) : null);
}

function pollParse(id, rerender, n = 0) {
  if (n > 40) return;
  setTimeout(async () => {
    try {
      const r = await get(`/resumes/${id}`);
      if (r.parse_status === "parsed" || r.parse_status === "failed") {
        if (r.parse_status === "parsed") toast("履歷解析完成");
        await loadMe();
        changed?.();
        if (current === "resume" && document.getElementById("scrim")) rerender();
      } else pollParse(id, rerender, n + 1);
    } catch { /* ignore */ }
  }, 1500);
}

const MCP_DESC = { notion: "匯出面試建議、題庫和報告到 Notion，也能讀取你的準備筆記。" };

async function mcp(body) {
  replace(body, h("div", { class: "center-load" }, "載入中⋯"));
  const res = await get("/mcp-connectors");
  if (!res.enabled) {
    replace(body, h("p", { class: "faint", style: "padding-top:12px" }, "連接功能尚未開放。"));
    return;
  }
  const status = (c) => !c.connected ? "尚未連接"
    : c.status === "error" ? "連線已失效，請重新連接"
    : `已連接${c.account_label ? `（${c.account_label}）` : ""}`;
  const connectBtn = (c, label) => h("button", { class: "btn pri sm", type: "button", onclick: async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    try { location.href = (await post(`/mcp-connectors/${c.provider}/start`)).authorize_url; }
    catch (err) { errorToast(err); btn.disabled = false; }
  } }, label);
  replace(body,
    h("p", { class: "faint", style: "padding-top:12px" }, "授權你自己的帳號給 Ava 使用。Ava 要建立或修改內容前，一定會先讓你確認。"),
    res.items.map((c) => h("div", { class: "srow" },
      h("div", null, h("div", { style: "font-weight:500" }, c.name),
        h("div", { class: "faint" }, MCP_DESC[c.provider] || ""),
        h("div", { class: c.status === "error" ? "form-err" : "faint" }, status(c))),
      !c.connected ? connectBtn(c, "連接")
        : h("div", { class: "row" }, c.status === "error" ? connectBtn(c, "重新連接") : null,
          h("button", { class: "btn out sm danger", type: "button", onclick: async () => {
            if (!(await confirmBox({ title: `中斷 ${c.name} 連線？`, text: "會刪除我們保存的授權，之後要使用需要重新連接。", ok: "中斷連線", danger: true }))) { openSettings("mcp"); return; }
            try { await del(`/mcp-connectors/${c.provider}`); toast(`已中斷 ${c.name} 連線`); } catch (err) { errorToast(err); }
            openSettings("mcp");
          } }, "中斷連線")))));
}

function account() {
  const me = S.me;
  const name = h("input", { class: "inp", value: me.user.display_name, "aria-label": "名稱", maxlength: 60 });
  name.addEventListener("change", async () => {
    if (!name.value.trim()) return;
    try { S.me = await patch("/me", { display_name: name.value.trim() }); renderSide(); toast("已儲存"); } catch (e) { errorToast(e); }
  });
  return [
    h("div", { class: "srow col" }, h("span", null, "名稱"), name),
    h("div", { class: "srow" }, h("span", null, "Email"), h("span", { class: "sv" }, me.user.email)),
    h("div", { class: "srow" }, h("span", null, "登入方式"), h("span", { class: "sv" },
      [me.user.has_password ? "Email 與密碼" : null, (me.user.providers || []).includes("google") ? "Google" : null].filter(Boolean).join("、") || "Email 與密碼")),
    h("div", { class: "srow" }, h("span", null, "方案"),
      h("span", { class: "sv" }, [me.membership?.plan_name || (me.user.plan === "pro" ? "Plus" : "Free"),
        me.usage.interviews_limit == null ? "面試不限次數" : `本月剩 ${Math.max(0, me.usage.interviews_limit - me.usage.interviews_used)} 場`,
        me.membership?.plan_expires_at ? `${fmtDay(me.membership.plan_expires_at)} 到期` : null].filter(Boolean).join("・"))),
    h("div", { class: "srow" }, h("span", null, "本月 AI 額度"),
      h("span", { class: "sv" }, me.usage.tokens_limit == null ? "不限"
        : `已用 ${Math.min(100, Math.round((me.usage.tokens_used / Math.max(1, me.usage.tokens_limit)) * 100))}%`
          + (me.membership?.resets_at ? `・${fmtDay(me.membership.resets_at)} 重置` : ""))),
    h("div", { class: "srow" }, h("span", null, "刪除所有面試紀錄與錄音"),
      h("button", { class: "btn out sm danger", type: "button", onclick: async () => {
        if (!(await confirmBox({ title: "刪除所有面試紀錄？", text: "所有面試報告、逐字稿和錄音都會刪除，無法復原。", ok: "全部刪除", danger: true }))) return;
        try { const r = await del("/me/interviews", { confirm: "DELETE" }); toast(`已刪除 ${r.deleted} 場面試`); await loadMe(); changed?.(); } catch (e) { errorToast(e); }
      } }, "全部刪除")),
    h("div", { class: "srow" }, h("span", null, "刪除帳號"),
      h("button", { class: "btn out sm danger", type: "button", onclick: () => deleteAccount() }, "刪除帳號")),
  ];
}

function deleteAccount() {
  const hasPw = S.me.user.has_password;
  const pw = hasPw ? h("input", { class: "inp", type: "password", placeholder: "輸入密碼確認", "aria-label": "密碼", autocomplete: "current-password" })
    : h("input", { class: "inp", type: "text", placeholder: "輸入 DELETE 確認", "aria-label": "確認文字", autocomplete: "off" });
  const err = h("div", { class: "form-err", role: "alert", style: "margin-top:8px" });
  openModal({ title: "刪除帳號？", cls: "sm", body: [
    h("p", { class: "muted", style: "margin-bottom:12px" }, "帳號、履歷、面試紀錄與錄音都會刪除，無法復原。"), pw, err,
    h("div", { class: "row", style: "justify-content:flex-end;margin-top:18px" },
      h("button", { class: "btn out", type: "button", onclick: closeModal }, "取消"),
      h("button", { class: "btn pri danger", type: "button", onclick: async () => {
        if (!hasPw && pw.value.trim() !== "DELETE") { err.textContent = "請輸入 DELETE"; return; }
        try { await del("/me", hasPw ? { password: pw.value } : { confirm: "DELETE" }); location.hash = ""; location.reload(); } catch (e) { err.textContent = e.message; }
      } }, "刪除帳號")),
  ] });
  pw.focus();
}

/** onChanged：設定改變時通知目前頁面重繪（例如新面試頁的預設值、履歷選單）。 */
export function openSettings(tab = "general", { onChanged } = {}) {
  if (onChanged) changed = onChanged;
  current = tab;
  const body = h("div", { class: "set-body" });
  const draw = () => {
    if (tab === "resume") resume(body, () => openSettings("resume")).catch(errorToast);
    else if (tab === "mcp") mcp(body).catch(errorToast);
    else replace(body, tab === "account" ? account() : general());
  };
  openModal({ title: "設定", bodyStyle: "padding:0", onClose: () => { current = null; },
    body: [h("div", { class: "set" },
      h("nav", { class: "set-tabs", "aria-label": "設定分類" }, TABS.map(([k, n, ic]) =>
        h("button", { type: "button", class: tab === k ? "on" : "", "aria-current": tab === k ? "page" : null, onclick: () => openSettings(k) }, icon(ic), n))),
      body)] });
  draw();
}

export function setSettingsListener(fn) { changed = fn; }
