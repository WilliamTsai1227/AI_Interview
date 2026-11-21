// 共用 UI：toast、彈出選單、對話框、確認框、頁首。
import { $, h, icon, replace } from "./dom.js";

export function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(t._h);
  t._h = setTimeout(() => t.classList.remove("show"), 2200);
}

export function errorToast(e) {
  toast(e?.message || "發生錯誤，請再試一次");
}

/** 複製文字：瀏覽器不允許時告訴使用者，不要假裝成功。 */
export async function copyText(text, ok = "已複製") {
  try { await navigator.clipboard.writeText(text); toast(ok); } catch { toast("無法複製，請手動選取文字"); }
}

const FOCUSABLE = 'a[href],button:not([disabled]),input:not([disabled]):not([type="hidden"]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])';
const focusables = (root) => [...root.querySelectorAll(FOCUSABLE)].filter((el) => !el.hidden && el.offsetParent !== null);

// ---------- 彈出選單 ----------
let menuEl = null;
function outside(e) { if (menuEl && !menuEl.contains(e.target)) closeMenu(); }
export function closeMenu({ restore = false } = {}) {
  if (!menuEl) return;
  const anchor = menuEl._anchor;
  menuEl.remove();
  menuEl = null;
  document.removeEventListener("mousedown", outside, true);
  if (restore && anchor?.isConnected) anchor.focus();
}

/**
 * items：{title} 小標｜{sep:true} 分隔線｜{header,label,sub} 帳號資訊｜{icon,label,sub,v,checked}
 */
export function openMenu(anchor, items, onPick, dir = "down") {
  closeMenu();
  const m = h("div", { class: "menu", role: "menu" });
  for (const it of items) {
    if (it.sep) m.append(h("hr"));
    else if (it.title) m.append(h("div", { class: "mh" }, it.title));
    else if (it.header) m.append(h("div", { style: "padding:8px 10px 6px" }, h("div", { style: "font-weight:500" }, it.label), h("div", { class: "faint" }, it.sub)));
    else {
      m.append(h("button", { type: "button", role: "menuitem", onclick: () => { closeMenu({ restore: true }); onPick(it.v, it); } },
        it.icon ? icon(it.icon) : null,
        h("span", { class: "mt" }, it.label, it.sub ? h("span", { class: "ms" }, it.sub) : null),
        it.checked !== undefined ? h("span", { class: "ck" }, it.checked ? icon("check") : null) : null));
    }
  }
  // 鍵盤：上下鍵移動、Home／End 到頭尾、Esc 或 Tab 關閉（Esc 會回到打開選單的按鈕）
  m.addEventListener("keydown", (e) => {
    const items = [...m.querySelectorAll('[role="menuitem"]')];
    const i = items.indexOf(document.activeElement);
    const go = (n) => { e.preventDefault(); items[(n + items.length) % items.length]?.focus(); };
    if (e.key === "ArrowDown") go(i + 1);
    else if (e.key === "ArrowUp") go(i - 1);
    else if (e.key === "Home") go(0);
    else if (e.key === "End") go(items.length - 1);
    else if (e.key === "Escape") { e.stopPropagation(); closeMenu({ restore: true }); }
    else if (e.key === "Tab") closeMenu();
  });
  m._anchor = anchor;
  document.body.append(m);
  menuEl = m;
  const r = anchor.getBoundingClientRect();
  const left = Math.min(Math.max(8, r.left), innerWidth - m.offsetWidth - 8);
  const top = dir === "up" || r.bottom + m.offsetHeight + 8 > innerHeight ? r.top - m.offsetHeight - 6 : r.bottom + 6;
  m.style.left = `${left}px`;
  m.style.top = `${Math.max(8, top)}px`;
  setTimeout(() => document.addEventListener("mousedown", outside, true));
  const first = m.querySelector('[role="menuitem"]');
  if (first) first.focus({ preventScroll: true });
  return m;
}

// ---------- 對話框 ----------
export function closeModal({ restore = true } = {}) {
  const s = $("#scrim");
  if (!s) return;
  s._onClose?.();
  s.remove();
  if (restore && s._prev?.isConnected) s._prev.focus();       // 焦點回到打開對話框的按鈕
}

/** openModal({ title, body: Node[], cls, onClose }) → scrim 元素 */
export function openModal({ title, body = [], cls = "", head, onClose, bodyStyle }) {
  // 換成另一個對話框（例如設定切換分頁、刪除確認）時，沿用最早打開它的按鈕，關閉後焦點回到那裡
  const prev = $("#scrim")?._prev || document.activeElement;
  closeModal({ restore: false });
  const close = h("button", { class: "ib", type: "button", "aria-label": "關閉", onclick: () => closeModal() }, icon("x"));
  const modal = h("div", { class: `modal ${cls}`, role: "dialog", "aria-modal": "true", "aria-labelledby": title ? "mTitle" : null },
    head || (title ? h("div", { class: "mhead" }, h("h2", { id: "mTitle" }, title), close) : null),
    h("div", { class: "mbody", style: bodyStyle }, body));
  const s = h("div", { class: "scrim", id: "scrim" }, modal);
  s._onClose = onClose;
  s._prev = prev;
  s.addEventListener("mousedown", (e) => { if (e.target === s) closeModal(); });
  // Tab 只在對話框裡循環，不會跑到後面的頁面
  modal.addEventListener("keydown", (e) => {
    if (e.key !== "Tab") return;
    const f = focusables(modal);
    if (!f.length) return;
    const [a, z] = [f[0], f[f.length - 1]];
    if (e.shiftKey && document.activeElement === a) { e.preventDefault(); z.focus(); }
    else if (!e.shiftKey && document.activeElement === z) { e.preventDefault(); a.focus(); }
  });
  document.body.append(s);
  // 預設焦點：內容裡第一個可操作的元素（確認框是「取消」，比較安全）；呼叫端要聚焦輸入框時會自己再 focus
  (focusables(modal.querySelector(".mbody"))[0] || focusables(modal)[0])?.focus({ preventScroll: true });
  return s;
}

/** 確認框：回傳 Promise<boolean> */
export function confirmBox({ title, text, ok = "確定", cancel = "取消", danger = false }) {
  return new Promise((resolve) => {
    let done = false;
    const finish = (v) => { if (!done) { done = true; resolve(v); } };
    openModal({
      title, cls: "sm", onClose: () => finish(false),
      body: [
        h("p", { class: "muted" }, text),
        h("div", { class: "row", style: "justify-content:flex-end;margin-top:18px" },
          h("button", { class: "btn out", type: "button", onclick: () => { finish(false); closeModal(); } }, cancel),
          h("button", { class: `btn pri${danger ? " danger" : ""}`, type: "button", onclick: () => { finish(true); closeModal(); } }, ok)),
      ],
    });
  });
}

// ---------- 頁首 ----------
export function setHead(iconName, title, badge, right = []) {
  replace($("#hTitle"), iconName ? icon(iconName) : null, h("span", null, title), badge ? h("span", { class: "badge", id: "hBadge" }, badge) : null);
  replace($("#hRight"), right);
}

export function seg(opts, cur, onPick, label) {
  return h("div", { class: "seg", role: "group", "aria-label": label }, opts.map(([k, label]) =>
    h("button", { type: "button", class: cur === k ? "on" : "", "aria-pressed": String(cur === k), onclick: () => onPick(k) }, label)));
}

export const backLink = (href, label) =>
  h("a", { class: "linkb", href, style: "margin:0 0 18px -6px" }, icon("left", 14), label);

export function logo(text, size = 44) {
  const fs = size >= 72 ? "1.8rem" : "1.05rem";
  const br = size >= 72 ? "20px" : "12px";
  return h("span", { class: "lg", "aria-hidden": "true", style: `width:${size}px;height:${size}px;font-size:${fs};border-radius:${br}${size >= 72 ? ";margin:0 auto" : ""}` }, text);
}
