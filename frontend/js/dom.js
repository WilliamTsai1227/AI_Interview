// DOM 建構工具：所有畫面都用 createElement / textContent 建立，不使用 innerHTML（避免 XSS）。
import { ICONS } from "./icons.js";

const SVG_NS = "http://www.w3.org/2000/svg";
const BOOL_PROPS = new Set(["disabled", "hidden", "checked", "open", "selected", "autofocus", "multiple", "readOnly"]);

/**
 * h("button", { class: "btn pri", onclick: fn, disabled: true }, "文字", icon("mic"))
 * - class / style（字串或物件）/ dataset（物件）/ on*（事件）/ 布林屬性 / 其他屬性
 * - 子節點：Node、字串、數字、陣列（攤平）、null / false / undefined（略過）
 */
export function h(tag, props, ...kids) {
  const el = document.createElement(tag);
  if (props) setProps(el, props);
  append(el, kids);
  return el;
}

export function setProps(el, props) {
  for (const [k, v] of Object.entries(props)) {
    if (v === undefined || v === null || v === false && !BOOL_PROPS.has(k)) continue;
    if (k === "class") el.className = v;
    else if (k === "style") typeof v === "string" ? (el.style.cssText = v) : Object.assign(el.style, v);
    else if (k === "dataset") Object.assign(el.dataset, v);
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (BOOL_PROPS.has(k)) el[k] = !!v;
    else if (k === "value") el.value = v;
    else if (k === "text") el.textContent = v;
    else el.setAttribute(k, v === true ? "" : String(v));
  }
}

export function append(el, kids) {
  for (const k of kids.flat(Infinity)) {
    if (k === null || k === undefined || k === false || k === true) continue;
    el.appendChild(k instanceof Node ? k : document.createTextNode(String(k)));
  }
  return el;
}

export function clear(el) {
  while (el.firstChild) el.removeChild(el.firstChild);
  return el;
}

export function replace(el, ...kids) {
  clear(el);
  return append(el, kids);
}

export function icon(name, size, extra) {
  const def = ICONS[name];
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  if (def?.fill) svg.setAttribute("fill", def.fill);
  else {
    svg.setAttribute("fill", "none");
    svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("stroke-width", String(def?.w ?? 1.75));
    svg.setAttribute("stroke-linecap", "round");
    svg.setAttribute("stroke-linejoin", "round");
  }
  if (size) { svg.setAttribute("width", size); svg.setAttribute("height", size); }
  if (extra) for (const [k, v] of Object.entries(extra)) svg.setAttribute(k, v);
  for (const [tag, attrs] of def?.els ?? []) {
    const c = document.createElementNS(SVG_NS, tag);
    for (const [k, v] of Object.entries(attrs)) c.setAttribute(k, v);
    svg.appendChild(c);
  }
  return svg;
}

export const $ = (s, root = document) => root.querySelector(s);
export const $$ = (s, root = document) => [...root.querySelectorAll(s)];

/** 在文字中把 needle 標成 <mark>（用文字節點組合，不用 innerHTML）。 */
export function highlight(text, needle) {
  const t = text || "";
  const i = needle ? t.indexOf(needle) : -1;
  if (i < 0) return [t];
  return [t.slice(0, i), h("mark", null, needle), t.slice(i + needle.length)];
}

export const dots = () => h("span", { class: "dots" }, h("i"), h("i"), h("i"));
export const skel = (n = 1, style) => Array.from({ length: n }, () => h("div", { class: "skel", style }));

/**
 * Enter 送出，但中文／日文輸入法選字時的 Enter 不算。
 * - Chrome、Firefox：選字時 isComposing = true
 * - Safari：選字確認的 Enter 送來時 isComposing 已是 false，但 keyCode = 229，
 *   且 compositionend 會比 keydown 先觸發，所以結束選字後的同一輪事件也要忽略
 * 用法：h("textarea", { onkeydown: onEnter(() => send()) })，並對同一元素呼叫 trackComposition(el)
 */
export function trackComposition(el) {
  el.addEventListener("compositionstart", () => { el._composing = true; });
  el.addEventListener("compositionend", () => { setTimeout(() => { el._composing = false; }, 0); });
  return el;
}

export function onEnter(fn, { allowShift = true } = {}) {
  return (e) => {
    if (e.key !== "Enter" || (allowShift && e.shiftKey)) return;
    if (e.isComposing || e.keyCode === 229 || e.target._composing) return;
    e.preventDefault();
    fn(e);
  };
}
