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

// ---------- 彈出選單 ----------
let menuEl = null;
function outside(e) { if (menuEl && !menuEl.contains(e.target)) closeMenu(); }
export function closeMenu() {
  if (menuEl) { menuEl.remove(); menuEl = null; document.removeEventListener("mousedown", outside, true); }
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
      m.append(h("button", { type: "button", role: "menuitem", onclick: () => { closeMenu(); onPick(it.v, it); } },
        it.icon ? icon(it.icon) : null,
        h("span", { class: "mt" }, it.label, it.sub ? h("span", { class: "ms" }, it.sub) : null),
        it.checked !== undefined ? h("span", { class: "ck" }, it.checked ? icon("check") : null) : null));
    }
  }
  document.body.append(m);
  menuEl = m;
  const r = anchor.getBoundingClientRect();
  const left = Math.min(Math.max(8, r.left), innerWidth - m.offsetWidth - 8);
  const top = dir === "up" || r.bottom + m.offsetHeight + 8 > innerHeight ? r.top - m.offsetHeight - 6 : r.bottom + 6;
  m.style.left = `${left}px`;
  m.style.top = `${Math.max(8, top)}px`;
  setTimeout(() => document.addEventListener("mousedown", outside, true));
  return m;
}

// ---------- 對話框 ----------
export function closeModal() {
  const s = $("#scrim");
  if (s) { s._onClose?.(); s.remove(); }
}

/** openModal({ title, body: Node[], cls, onClose }) → scrim 元素 */
export function openModal({ title, body = [], cls = "", head, onClose, bodyStyle }) {
  closeModal();
  const close = h("button", { class: "ib", type: "button", "aria-label": "關閉", onclick: closeModal }, icon("x"));
  const modal = h("div", { class: `modal ${cls}`, role: "dialog", "aria-modal": "true" },
    head || (title ? h("div", { class: "mhead" }, h("h2", null, title), close) : null),
    h("div", { class: "mbody", style: bodyStyle }, body));
  const s = h("div", { class: "scrim", id: "scrim" }, modal);
  s._onClose = onClose;
  s.addEventListener("mousedown", (e) => { if (e.target === s) closeModal(); });
  document.body.append(s);
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

export function seg(opts, cur, onPick) {
  return h("div", { class: "seg", role: "group" }, opts.map(([k, label]) =>
    h("button", { type: "button", class: cur === k ? "on" : "", "aria-pressed": String(cur === k), onclick: () => onPick(k) }, label)));
}

export const backLink = (href, label) =>
  h("a", { class: "linkb", href, style: "margin:0 0 18px -6px" }, icon("left", 14), label);

export function logo(text, size = 44) {
  const fs = size >= 72 ? "1.8rem" : "1.05rem";
  const br = size >= 72 ? "20px" : "12px";
  return h("span", { class: "lg", style: `width:${size}px;height:${size}px;font-size:${fs};border-radius:${br}${size >= 72 ? ";margin:0 auto" : ""}` }, text);
}
