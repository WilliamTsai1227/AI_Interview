// 職缺詳情視窗：只顯示事實與 Ava 整理的摘要；一律提供「前往原始職缺頁」。
import { del, get, post } from "../api.js";
import { h, icon } from "../dom.js";
import { renderSide } from "../shell.js";
import { ago, loadMe, loadTargets, pickTarget } from "../store.js";
import { closeModal, errorToast, logo, openModal, toast } from "../ui.js";

const MODE = { onsite: "進辦公室", hybrid: "混合辦公", remote: "完全遠端" };

export const jobType = (j) => [j.employment_type || "全職", MODE[j.work_mode] || j.work_mode_note].filter(Boolean).join("・");

export function sourceLine(j) {
  if (j.source === "user") return h("div", { class: "faint", style: "margin-top:10px" }, "這是你貼上的職缺內容");
  return h("div", { class: "faint", style: "margin-top:10px" },
    `資料來源：${j.source_site_label || j.source_site || "外部網站"}・${ago(j.last_verified_at)}確認仍在招募・內容為 Ava 整理的摘要`);
}

export function sourceButton(j, cls = "btn out") {
  if (!j.source_url) return null;
  return h("a", { class: `${cls} src-link`, href: j.source_url, target: "_blank", rel: "noopener noreferrer" }, "前往原始職缺頁 ↗");
}

/** 確保職缺是目標職缺（不是就加入），回傳 target id。 */
export async function ensureTarget(j) {
  if (j.target_id) return j.target_id;
  const t = await post("/targets", { job_post_id: j.id });
  await Promise.all([loadTargets(), loadMe()]);
  renderSide();
  return t.id;
}

export async function openJob(jobId, { onChange } = {}) {
  let j;
  try { j = await get(`/jobs/${jobId}`); } catch (e) { errorToast(e); return; }
  const go = async (where) => {
    try {
      const tid = await ensureTarget(j);
      pickTarget(tid);
      closeModal();
      location.hash = where === "new" ? "#new" : `#${where}/${tid}`;
      if (!j.target_id) toast("已加入目標職缺，Ava 正在出題");
    } catch (e) { errorToast(e); }
  };
  const tags = h("div", { class: "row", style: "justify-content:center;margin-top:12px" },
    j.match_score != null ? h("span", { class: "tagx" }, `${j.match_score}% 契合`) : null,
    h("span", { class: "tagx" }, jobType(j)), j.salary_text ? h("span", { class: "tagx" }, j.salary_text) : null,
    j.target_id ? h("span", { class: "tagx" }, "目標職缺") : null);
  openModal({ title: "職缺詳情", cls: "sm", body: [
    h("div", { style: "text-align:center" }, logo(j.logo_text, 72),
      h("h2", { style: "font-size:1.4rem;font-weight:600;margin-top:14px" }, j.title),
      h("div", { class: "muted", style: "margin-top:2px" }, `${j.company_name}・${j.location_text || j.city || ""}`),
      tags, sourceLine(j),
      j.source_url ? h("div", { style: "margin-top:12px" }, sourceButton(j, "btn out sm")) : null),
    h("div", { class: "doc", style: "margin-top:22px" },
      h("p", { class: "muted" }, j.summary || j.about || ""),
      j.duties.length ? [h("div", { class: "sub-h" }, "工作內容"), h("ul", null, j.duties.map((d) => h("li", null, d)))] : null,
      j.requirements.length ? [h("div", { class: "sub-h" }, "條件要求"), h("ul", null, j.requirements.map((d) => h("li", null, d)))] : null,
      h("div", { class: "row", style: "margin-top:14px" }, j.tags.map((t) => h("span", { class: "tagx" }, t)))),
    h("div", { class: "row", style: "margin-top:24px;justify-content:center" },
      h("button", { class: "btn pri", type: "button", onclick: () => go("new") }, icon("mic"), "練習這個職缺"),
      h("button", { class: "btn out", type: "button", onclick: () => go("questions") }, icon("book"), "面試題庫"),
      h("button", { class: "btn out", type: "button", onclick: () => go("prep") }, icon("bulb"), "面試建議")),
    j.target_id
      ? h("div", { style: "text-align:center;margin-top:14px" },
        h("button", { class: "linkb", type: "button", onclick: () => { closeModal(); location.hash = `#targets/${j.target_id}`; } }, "查看準備進度"), "・",
        h("button", { class: "linkb", type: "button", onclick: async () => {
          try {
            await del(`/targets/${j.target_id}`);
            await loadTargets();
            closeModal();
            toast("已移出目標職缺，面試題庫會保留，重新加入就能找回");
            onChange?.();
          } catch (e) { errorToast(e); }
        } }, "移出目標職缺"))
      : h("p", { class: "faint", style: "text-align:center;margin-top:14px" },
        "練習後會自動加入目標職缺，Ava 會先幫你出好題目。職缺庫只有摘要，到原始職缺頁複製完整內容貼上，題目會更準。"),
  ] });
}
