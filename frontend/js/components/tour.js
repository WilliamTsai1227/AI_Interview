// 新手導覽：第一次用某個畫面時，把要介紹的按鈕挖空打亮，旁邊跳出說明框（第幾步／上一步／下一步／略過）。
// 看過哪些導覽記在瀏覽器（依帳號分開）；帳號選單的「使用教學」會清掉紀錄、從頭再看一次。
// 步驟：{ el: CSS 選擇器或回傳元素的函式（沒有就置中顯示）, title, body, side: 目標在側欄（手機要先打開側欄、收合時先展開） }
import { $, h, icon } from "../dom.js";
import { mobile } from "../shell.js";
import { S } from "../store.js";

const PAD = 6;          // 打亮的範圍比目標大一點
const GAP = 12;         // 說明框和目標的距離
let active = null;

const key = () => `ai-tours:${S.me?.user?.id || "anon"}`;
function seen() {
  try { return new Set(JSON.parse(localStorage.getItem(key()) || "[]")); } catch { return new Set(); }
}
function markSeen(id) {
  const s = seen();
  s.add(id);
  try { localStorage.setItem(key(), JSON.stringify([...s])); } catch { /* 無痕模式：這次看完就算了 */ }
}
export function resetTours() {
  try { localStorage.removeItem(key()); } catch { /* ignore */ }
}

/** 沒看過才播放；delay 讓頁面先畫好。回傳是否有播放。 */
export function maybeTour(id, steps, delay = 400) {
  if (seen().has(id) || active) return false;
  setTimeout(() => { if (!active && !seen().has(id)) startTour(id, steps); }, delay);
  return true;
}

export function startTour(id, steps) {
  stopTour(false);
  const app = $("#app");
  const prevFocus = document.activeElement;
  const restore = { collapsed: app.classList.contains("collapsed"), open: app.classList.contains("open") };
  const spot = h("div", { class: "tour-spot", "aria-hidden": "true" });
  const pop = h("div", { class: "tour-pop", role: "dialog", "aria-modal": "true", "aria-labelledby": "tourTitle", "aria-describedby": "tourBody" });
  const root = h("div", { class: "tour" }, spot, pop);
  // 點暗掉的地方不會誤觸後面的按鈕；導覽只用說明框上的按鈕操作
  root.addEventListener("mousedown", (e) => { if (!pop.contains(e.target)) e.preventDefault(); });
  document.body.append(root);
  let i = 0, target = null, raf = 0;

  const resolve = (s) => {
    const el = typeof s.el === "function" ? s.el() : s.el ? document.querySelector(s.el) : null;
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return r.width && r.height ? el : null;
  };

  function place() {
    const vw = innerWidth, vh = innerHeight;
    const pw = pop.offsetWidth, ph = pop.offsetHeight;
    if (!target) {          // 沒有目標：置中、整個畫面暗掉
      Object.assign(spot.style, { left: `${vw / 2}px`, top: `${vh / 2}px`, width: "0px", height: "0px" });
      Object.assign(pop.style, { left: `${Math.max(16, (vw - pw) / 2)}px`, top: `${Math.max(16, (vh - ph) / 2)}px` });
      pop.dataset.at = "center";
      return;
    }
    const r = target.getBoundingClientRect();
    Object.assign(spot.style, { left: `${r.left - PAD}px`, top: `${r.top - PAD}px`, width: `${r.width + PAD * 2}px`, height: `${r.height + PAD * 2}px` });
    // 依序試：下、上、右、左；都放不下就貼著畫面底部
    const fits = {
      below: r.bottom + GAP + ph <= vh - 12, above: r.top - GAP - ph >= 12,
      right: r.right + GAP + pw <= vw - 12, left: r.left - GAP - pw >= 12,
    };
    const at = fits.below ? "below" : fits.above ? "above" : fits.right ? "right" : fits.left ? "left" : "bottom";
    let x, y;
    if (at === "below" || at === "above") {
      x = r.left + r.width / 2 - pw / 2;
      y = at === "below" ? r.bottom + GAP : r.top - GAP - ph;
    } else if (at === "right" || at === "left") {
      x = at === "right" ? r.right + GAP : r.left - GAP - pw;
      y = r.top + r.height / 2 - ph / 2;
    } else { x = (vw - pw) / 2; y = vh - ph - 16; }
    pop.style.left = `${Math.min(Math.max(12, x), vw - pw - 12)}px`;
    pop.style.top = `${Math.min(Math.max(12, y), vh - ph - 12)}px`;
    pop.dataset.at = at;
  }
  const follow = () => { cancelAnimationFrame(raf); raf = requestAnimationFrame(place); };

  function show(n) {
    i = n;
    const s = steps[i];
    if (s.side) {           // 目標在側欄：手機打開抽屜、桌機收合時先展開
      if (mobile()) app.classList.add("open"); else app.classList.remove("collapsed");
    } else if (mobile()) app.classList.remove("open");
    target = resolve(s);
    target?.scrollIntoView({ block: "nearest" });
    const last = i === steps.length - 1;
    const counted = steps.length > 1;
    pop.replaceChildren(
      h("div", { class: "tour-h" },
        counted ? h("span", { class: "tour-n num" }, `${i + 1} / ${steps.length}`) : null,
        h("button", { class: "ib sm", type: "button", "aria-label": "關閉導覽", onclick: () => stopTour() }, icon("x"))),
      h("h2", { id: "tourTitle" }, s.title),
      h("p", { id: "tourBody" }, s.body),
      h("div", { class: "tour-f" },
        counted ? h("div", { class: "tour-dots", "aria-hidden": "true" }, steps.map((_, k) => h("i", { class: k === i ? "on" : k < i ? "done" : "" }))) : h("span"),
        h("div", { class: "row" },
          i ? h("button", { class: "btn ghost sm", type: "button", onclick: () => show(i - 1) }, "上一步")
            : h("button", { class: "btn ghost sm", type: "button", onclick: () => stopTour() }, "略過"),
          h("button", { class: "btn pri sm", type: "button", "data-next": "", onclick: () => (last ? stopTour() : show(i + 1)) },
            last ? (s.done || "開始使用") : (s.next || "下一步")))));
    root.classList.toggle("no-target", !target);
    place();
    pop.querySelector("[data-next]").focus({ preventScroll: true });
  }

  function onKey(e) {
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); stopTour(); }
    else if (e.key === "ArrowRight") { e.preventDefault(); if (i < steps.length - 1) show(i + 1); }
    else if (e.key === "ArrowLeft") { e.preventDefault(); if (i) show(i - 1); }
    else if (e.key === "Tab") {           // 焦點只在說明框裡循環
      const f = [...pop.querySelectorAll("button")];
      const [a, z] = [f[0], f[f.length - 1]];
      if (e.shiftKey && document.activeElement === a) { e.preventDefault(); z.focus(); }
      else if (!e.shiftKey && document.activeElement === z) { e.preventDefault(); a.focus(); }
    }
  }
  document.addEventListener("keydown", onKey, true);
  addEventListener("resize", follow);
  addEventListener("scroll", follow, true);

  active = {
    id,
    end(save) {
      document.removeEventListener("keydown", onKey, true);
      removeEventListener("resize", follow);
      removeEventListener("scroll", follow, true);
      cancelAnimationFrame(raf);
      root.remove();
      app.classList.toggle("collapsed", restore.collapsed);
      app.classList.toggle("open", restore.open);
      if (save) markSeen(id);
      if (prevFocus?.isConnected) prevFocus.focus({ preventScroll: true });
    },
  };
  show(0);
}

/** 結束目前的導覽（略過、看完、換頁都算看過，下次不再自動出現）。 */
export function stopTour(save = true) {
  const a = active;
  active = null;
  a?.end(save);
}
