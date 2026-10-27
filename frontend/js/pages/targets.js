// 目標職缺：列表 → 詳情（準備進度：題庫、建議、報告；職缺內容；移出）。
import { del, get } from "../api.js";
import { addTargetMenuItems, onAddTargetPick, openJd } from "../components/jd.js";
import { jobType, sourceButton, sourceLine } from "../components/job.js";
import { dots, h, icon, replace } from "../dom.js";
import { renderSide } from "../shell.js";
import { fmtDate, loadMe, loadTargets, pickTarget, S } from "../store.js";
import { confirmBox, errorToast, logo, openMenu, setHead, toast } from "../ui.js";

export async function render(view, tid, ctx) {
  return tid ? detail(view, tid, ctx) : list(view, ctx);
}

async function list(view, ctx) {
  setHead("target", "目標職缺");
  const wrap = h("div", { class: "wrap doc" });
  view.append(wrap);
  await loadTargets().catch(() => {});
  if (!ctx.isCurrent()) return;
  const tg = S.targets || [];
  replace(wrap, h("h1", null, "目標職缺"),
    h("p", { class: "muted", style: "margin-top:6px" }, "你正在準備的職缺。每個目標職缺都有自己的面試題庫、面試建議和面試報告。"),
    h("div", { style: "margin-top:20px" },
      tg.map((t) => h("a", { class: "hrow", href: `#targets/${t.id}` }, logo(t.job.company_name.slice(0, 1).toUpperCase()),
        h("span", { class: "bd" }, h("b", null, `${t.job.company_name}・${t.job.title}`),
          h("span", { class: "m" }, [t.match_score != null ? `契合度 ${t.match_score}%` : null,
            t.question_set.status === "preparing" ? "Ava 出題中⋯" : `${t.question_set.item_count} 題`,
            t.last_report ? `最近練習 ${fmtDate(t.last_report.ended_at)}・${t.last_report.overall_score} 分` : "還沒練習"].filter(Boolean).join("・"))),
        h("span", { class: "ch" }, icon("right")))),
      tg.length ? h("button", { class: "addrow", type: "button", onclick: (e) => openMenu(e.currentTarget, addTargetMenuItems(), onAddTargetPick) }, icon("plus"), "新增目標職缺")
        : h("div", { class: "empty-note" }, h("p", null, "還沒有目標職缺。貼上你想應徵的職缺，或到「職缺搜尋」挑一個。"),
          h("button", { class: "btn pri", type: "button", onclick: () => openJd() }, icon("plus"), "貼上職缺內容"))));
}

async function detail(view, tid, ctx) {
  setHead("target", "目標職缺");
  const wrap = h("div", { class: "wrap doc" }, h("div", { class: "center-load" }, dots()));
  view.append(wrap);
  let t;
  try { t = await get(`/targets/${tid}`); } catch {
    if (ctx.isCurrent()) replace(wrap, h("h1", null, "找不到這個目標職缺"), h("a", { class: "linkb", href: "#targets" }, "回到目標職缺"));
    return;
  }
  if (!ctx.isCurrent()) return;
  pickTarget(tid);
  const j = t.job;
  // 使用者自己貼的職缺：可以展開看當初貼上的原文（平台職缺的原文只給 AI 用，API 不會回傳）
  const raw = j.source === "user" && j.raw_text ? h("details", { class: "acc raw-jd", id: "rawJd" },
    h("summary", null, icon("file"), h("span", { class: "t" }, "你貼上的原始職缺內容"),
      h("span", { class: "faint" }, `${j.raw_text.length} 字`), icon("chev")),
    h("div", { class: "bd" }, h("div", { class: "raw-text" }, j.raw_text),
      h("div", { class: "row", style: "margin-top:10px" },
        h("button", { class: "btn out sm", type: "button", onclick: () => { navigator.clipboard?.writeText(j.raw_text); toast("已複製原始內容"); } },
          icon("copy"), "複製全文")))) : null;
  const showRaw = () => { raw.open = true; raw.scrollIntoView({ behavior: "smooth", block: "start" }); };
  const chev = () => icon("right", 16, { style: "color:var(--ink-3)" });
  const progressRow = (k, href, main, hint) => h("a", { class: "su-row", href, style: "display:flex" },
    h("span", { class: "k" }, k), h("div", { class: "v" }, h("span", null, main), hint ? h("span", { class: "hint" }, hint) : null), chev());
  replace(wrap,
    h("a", { class: "linkb", href: "#targets", style: "margin:0 0 18px -6px" }, icon("left", 14), "所有目標職缺"),
    h("div", { style: "text-align:center;margin-top:8px" }, logo(j.company_name.slice(0, 1).toUpperCase(), 72),
      h("h1", { style: "font-size:1.6rem;margin-top:14px" }, j.title),
      h("div", { class: "muted", style: "margin-top:2px" }, [j.company_name, j.location_text].filter(Boolean).join("・")),
      h("div", { class: "row", style: "justify-content:center;margin-top:12px" },
        t.match_score != null ? h("span", { class: "tagx" }, `${t.match_score}% 契合`) : null,
        j.source !== "user" ? h("span", { class: "tagx" }, jobType(j)) : null, j.salary_text ? h("span", { class: "tagx" }, j.salary_text) : null),
      raw ? h("div", { class: "faint", style: "margin-top:10px" }, "這是你貼上的職缺內容・",
        h("button", { class: "linkb", type: "button", style: "display:inline-flex;text-decoration:underline", onclick: showRaw }, "查看原文"))
        : sourceLine(j),
      h("div", { class: "row", style: "justify-content:center;margin-top:20px" },
        h("a", { class: "btn pri", href: "#new" }, icon("mic"), "練習這個職缺"),
        h("a", { class: "btn out", href: `#questions/${tid}` }, icon("book"), "面試題庫"),
        h("a", { class: "btn out", href: `#prep/${tid}` }, icon("bulb"), "面試建議"),
        sourceButton(j))),
    h("div", { class: "basis", style: "margin-top:28px" },
      h("div", { class: "basis-h" }, icon("target"), "準備進度"),
      progressRow("題庫", `#questions/${tid}`, t.question_set.status === "preparing" ? "Ava 出題中⋯" : `${t.question_set.item_count} 題`, "模擬面試會照順序提問"),
      progressRow("建議", `#prep/${tid}`, t.prep.status === "ready" ? `${t.prep.likely_count} 個可能題目・清單完成 ${t.prep.checklist_done}/${t.prep.checklist_total}` : "Ava 正在分析⋯"),
      progressRow("報告", t.last_report ? `#report/${t.last_report.session_id}` : "#new",
        t.last_report ? `最近 ${t.last_report.overall_score} 分（${fmtDate(t.last_report.ended_at)}）` : "還沒練習過")),
    h("h2", null, "關於這個職缺"),
    j.parse_status !== "parsed" && j.source === "user" ? h("p", { class: "muted" }, j.parse_status === "failed" ? "Ava 沒有解析成功，題目會依你貼上的原文出。" : "Ava 正在整理職缺內容⋯")
      : h("p", { class: "muted" }, j.summary || j.about || ""),
    h("div", { class: "grid2" },
      h("div", null, h("div", { class: "sub-h" }, "工作內容"), h("ul", null, (j.duties || []).map((d) => h("li", null, d)))),
      h("div", null, h("div", { class: "sub-h" }, "條件要求"), h("ul", null, (j.requirements || []).map((d) => h("li", null, d))))),
    j.tags.length ? h("div", { class: "row", style: "margin-top:16px" }, j.tags.map((x) => h("span", { class: "tagx" }, x))) : null,
    raw,
    h("div", { style: "text-align:center;margin-top:36px" }, h("button", { class: "linkb", type: "button", onclick: async () => {
      const ok = await confirmBox({ title: "移出目標職缺？", text: "面試題庫、面試建議和報告都會保留，之後重新加入就能找回。", ok: "移出" });
      if (!ok) return;
      try {
        await del(`/targets/${tid}`);
        await Promise.all([loadTargets(), loadMe()]);
        renderSide();
        location.hash = "#targets";
        toast("已移出目標職缺");
      } catch (e) { errorToast(e); }
    } }, "移出目標職缺")));
}
