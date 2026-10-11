// ⌘K 搜尋：面試報告、目標職缺、職缺搜尋。
import { get } from "../api.js";
import { h, icon, replace } from "../dom.js";
import { S } from "../store.js";
import { closeModal, openModal } from "../ui.js";
import { openJob } from "./job.js";

export function openSearch() {
  const res = h("div", { class: "mbody", style: "padding:8px" });
  const inp = h("input", { class: "inp", style: "border:0;padding:6px 4px;font-size:1rem", placeholder: "搜尋面試報告、目標職缺、職缺⋯", "aria-label": "搜尋" });
  const head = h("div", { class: "mhead", style: "padding:8px 12px 8px 18px" },
    h("span", { style: "color:var(--ink-3)" }, icon("search", 18)), inp,
    h("button", { class: "ib", type: "button", "aria-label": "關閉", onclick: closeModal }, icon("x")));
  const s = openModal({ head, cls: "sm" });
  s.querySelector(".mbody").replaceWith(res);
  let timer = null, seq = 0;
  const draw = async (q) => {
    const my = ++seq;
    const ql = q.toLowerCase();
    const targets = (S.targets || []).filter((t) => !ql || `${t.job.company_name} ${t.job.title}`.toLowerCase().includes(ql))
      .slice(0, 5).map((t) => ({ ic: "target", t: `${t.job.company_name}・${t.job.title}`, r: `${t.question_set.item_count} 題`, href: `#targets/${t.id}` }));
    let reports = [], jobs = [];
    try {
      const [r, j] = await Promise.all([get(`/interviews?limit=5${q ? `&q=${encodeURIComponent(q)}` : ""}`), q ? get(`/jobs?limit=5&q=${encodeURIComponent(q)}`) : null]);
      reports = r.items.map((x) => ({ ic: "report", t: x.job_label, r: x.overall_score != null ? `${x.overall_score} 分` : "評分中", href: `#report/${x.id}` }));
      jobs = (j?.items || []).map((x) => ({ ic: "brief", t: `${x.company_name}・${x.title}`, r: x.match_score != null ? `${x.match_score}%` : "", job: x.id }));
    } catch { /* 離線時只顯示目標職缺 */ }
    if (my !== seq) return;
    const all = [...targets, ...reports, ...jobs].slice(0, 12);
    replace(res, all.length ? all.map((x) => h("button", { class: "sres", type: "button", onclick: () => {
      closeModal();
      if (x.job) openJob(x.job); else location.hash = x.href;
    } }, icon(x.ic), h("span", { class: "t" }, x.t), h("span", { class: "faint" }, x.r)))
      : h("p", { class: "faint", style: "padding:16px" }, "找不到結果"));
  };
  inp.oninput = () => { clearTimeout(timer); timer = setTimeout(() => draw(inp.value.trim()), 200); };
  draw("");
  inp.focus();
}
