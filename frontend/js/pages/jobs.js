// 職缺搜尋：平台職缺（只存事實與摘要，詳情一律導回原始職缺頁）；想應徵的不在這裡就直接貼上。
import { get } from "../api.js";
import { openJd } from "../components/jd.js";
import { jobType, openJob } from "../components/job.js";
import { dots, h, icon, replace } from "../dom.js";
import { errorToast, logo, setHead } from "../ui.js";

const TABS = [["all", "全部"], ["target", "目標職缺"], ["foreign", "外商"], ["remote", "遠端"], ["hybrid", "混合辦公"], ["match80", "契合度 80%+"]];
const st = { tab: "all", q: "" };

export async function render(view, _arg, ctx) {
  setHead("search", "職缺搜尋", null, [h("button", { class: "hbtn", type: "button", onclick: () => openJd() }, icon("plus"), h("span", { class: "hide-m" }, "貼上職缺"))]);
  const grid = h("div", { class: "jgrid", id: "jgrid" });
  const tabs = h("div", { class: "tabs" });
  let seq = 0, timer = null;

  async function load() {
    const my = ++seq;
    replace(grid, h("div", { class: "center-load", style: "grid-column:1/-1" }, dots()));
    const qs = new URLSearchParams({ limit: "40" });
    if (st.q) qs.set("q", st.q);
    if (st.tab !== "all") qs.set("filters", st.tab);
    let r;
    try { r = await get(`/jobs?${qs}`); } catch (e) { errorToast(e); return; }
    if (my !== seq || !ctx.isCurrent()) return;
    replace(grid, r.items.length ? r.items.map((j) => h("button", { class: "job", type: "button", onclick: () => openJob(j.id, { onChange: load }) },
      logo(j.logo_text, 52), h("span", { class: "bd" }, h("b", null, j.title),
        h("span", { class: "s" }, [j.company_name, (j.city || j.location_text || "").slice(0, 3), jobType(j).split("・")[1] || jobType(j)].filter(Boolean).join("・")),
        h("span", { class: "m" }, [j.match_score != null ? `${j.match_score}% 契合` : "上傳履歷看契合度", j.target_id ? "目標職缺" : null].filter(Boolean).join("・")))))
      : h("p", { class: "muted", style: "padding:20px 14px;grid-column:1/-1" }, "找不到符合的職缺，換個關鍵字試試看。"));
  }

  const drawTabs = () => replace(tabs, TABS.map(([k, n]) => h("button", { type: "button", class: st.tab === k ? "on" : "", onclick: () => { st.tab = k; drawTabs(); load(); } }, n)));
  drawTabs();
  const input = h("input", { id: "jq", placeholder: "搜尋職稱、公司或技能", value: st.q, "aria-label": "搜尋職缺",
    oninput: () => { clearTimeout(timer); timer = setTimeout(() => { st.q = input.value.trim(); load(); }, 250); } });
  view.append(h("div", { class: "wrap wide" },
    h("div", { class: "jhead" }, h("h1", null, "職缺搜尋"),
      h("p", null, "依你的履歷計算契合度。想應徵的職缺不在這裡？直接",
        h("button", { class: "linkb", type: "button", onclick: () => openJd(), style: "display:inline-flex;text-decoration:underline" }, "貼上職缺內容"), "。")),
    h("label", { class: "search" }, icon("search"), input), tabs, grid));
  await load();
  return () => clearTimeout(timer);
}
