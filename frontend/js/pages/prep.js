// 面試建議：先列目標職缺 → 單一職缺的建議（依據卡片、契合度、優勢／補強、高機率方向、可能題目、準備清單）。
// 「重新分析」與「再多產生」都只新增、不刪除。
import { get, patch, post } from "../api.js";
import { addTargetMenuItems, onAddTargetPick, openJd } from "../components/jd.js";
import { openJob } from "../components/job.js";
import { openSettings } from "../components/settings.js";
import { dots, h, icon, onEnter, replace, skel, trackComposition } from "../dom.js";
import { CATS, fmtDate, loadResumes, loadTargets, pickTarget, S } from "../store.js";
import { errorToast, logo, openMenu, setHead, toast } from "../ui.js";

const PRI = { high: "高", medium: "中", low: "低" };

export async function render(view, tid, ctx) {
  return tid ? detail(view, tid, ctx) : list(view, ctx);
}

async function list(view, ctx) {
  setHead("bulb", "面試建議");
  const wrap = h("div", { class: "wrap doc" });
  view.append(wrap);
  await loadTargets().catch(() => {});
  if (!ctx.isCurrent()) return;
  const tg = S.targets || [];
  replace(wrap, h("h1", null, "面試建議"),
    h("p", { class: "muted", style: "margin-top:6px" }, "選一個目標職缺。Ava 會比對職缺內容和你的履歷，整理優勢、需要補強的地方、可能被問的題目和準備清單。"),
    h("div", { style: "margin-top:20px" },
      tg.map((t) => h("a", { class: "hrow", href: `#prep/${t.id}` }, logo(t.job.company_name.slice(0, 1).toUpperCase()),
        h("span", { class: "bd" }, h("b", null, `${t.job.company_name}・${t.job.title}`),
          h("span", { class: "m" }, t.prep.status === "ready"
            ? `${t.match_score != null ? `契合度 ${t.match_score}%・` : ""}${t.prep.likely_count} 個可能題目・清單完成 ${t.prep.checklist_done}/${t.prep.checklist_total}`
            : "Ava 正在比對履歷和職缺⋯")),
        h("span", { class: "ch" }, icon("right")))),
      tg.length ? h("button", { class: "addrow", type: "button", onclick: (e) => openMenu(e.currentTarget, addTargetMenuItems(), onAddTargetPick) }, icon("plus"), "新增目標職缺")
        : h("div", { class: "empty-note" }, h("p", null, "加入目標職缺後，Ava 會自動幫你整理面試建議。"),
          h("button", { class: "btn pri", type: "button", onclick: () => openJd() }, icon("plus"), "貼上職缺內容"))));
}

async function detail(view, tid, ctx) {
  pickTarget(tid);
  setHead("bulb", "面試建議");
  const wrap = h("div", { class: "wrap doc" }, h("div", { class: "center-load" }, dots()));
  view.append(wrap);
  const ui = { more: null, cat: "", n: 3, busy: null };
  let prep, target, poll = null;

  async function load() {
    [prep, target] = await Promise.all([get(`/targets/${tid}/prep`), get(`/targets/${tid}`)]);
    if (!S.resumes) await loadResumes().catch(() => {});
  }
  try { await load(); } catch (e) {
    if (ctx.isCurrent()) replace(wrap, h("h1", null, "找不到這份面試建議"), h("a", { class: "linkb", href: "#prep" }, "回到面試建議"));
    return;
  }
  if (!ctx.isCurrent()) return;

  function watch(genId, kind) {
    clearTimeout(poll);
    poll = setTimeout(async () => {
      if (!ctx.isCurrent()) return;
      try {
        if (genId) {
          const g = await get(`/prep-generations/${genId}`);
          if (g.status === "succeeded" || g.status === "failed") {
            ui.busy = null;
            await load();
            draw();
            if (g.status === "failed") toast("Ava 這次沒有產生成功，請再試一次");
            else if (kind === "analyze") toast("已重新分析。原本的題目和清單都保留，新發現的放在最後");
            else toast(g.created_count ? `新增了 ${g.created_count} ${kind === "likely_more" ? "題" : "項"}，原本的都保留` : "沒有新的項目了，換個方向試試");
            return;
          }
        } else {
          await load();
          draw();
          if (prep.status === "ready" && !prep.pending_generation) return;
        }
      } catch { /* ignore */ }
      watch(genId, kind);
    }, 1500);
  }

  async function analyze() {
    try {
      const r = await post(`/targets/${tid}/prep/analyze`, {});
      ui.busy = "analyze";
      draw();
      watch(r.generation_id, "analyze");
    } catch (e) { errorToast(e); }
  }

  async function generateMore(kind, note) {
    if (ui.busy) return;
    try {
      const body = kind === "likely" ? { note, category: ui.cat || null, count: ui.n } : { note, count: ui.n };
      const r = await post(`/preps/${prep.id}/${kind === "likely" ? "likely-questions" : "checklist"}/generate`, body);
      ui.busy = kind;
      ui.more = null;
      draw();
      watch(r.generation_id, kind === "likely" ? "likely_more" : "checklist_more");
    } catch (e) { errorToast(e); }
  }

  async function addToSet(ids) {
    try {
      const r = await post(`/preps/${prep.id}/likely-questions/add-to-set`, ids ? { ids } : {});
      await load();
      draw();
      toast(r.added ? `${r.added} 題已加入 ${target.job.company_name} 面試題庫` : "這些題目已經在面試題庫裡了");
    } catch (e) { errorToast(e); }
  }

  function moreComposer(kind) {
    if (ui.more !== kind) return null;
    const ta = trackComposition(h("textarea", { rows: 1, "aria-label": "想新增的方向",
      placeholder: kind === "likely" ? "還想準備哪方面的題目？例如：多一些系統設計、帶人經驗" : "還想準備什麼？例如：薪資談判、反問面試官的問題",
      onkeydown: onEnter(() => generateMore(kind, ta.value.trim())) }));
    setTimeout(() => ta.focus());
    return h("div", { class: "composer", style: "margin:0 0 14px" }, ta, h("div", { class: "c-bar" },
      kind === "likely" ? h("button", { class: "pill", type: "button", onclick: (e) => openMenu(e.currentTarget,
        [{ title: "領域" }, { label: "領域不限", v: "", checked: !ui.cat }, ...CATS.map((c) => ({ label: c, v: c, checked: ui.cat === c }))], (v) => { ui.cat = v; draw(); }) },
      icon("sliders"), h("span", { class: "lbl" }, ui.cat || "領域不限"), icon("chev")) : null,
      h("button", { class: "pill", type: "button", onclick: (e) => openMenu(e.currentTarget,
        [{ title: "新增幾個" }, ...[3, 5, 8].map((n) => ({ label: `${n} 個`, v: n, checked: ui.n === n }))], (v) => { ui.n = v; draw(); }) },
      h("span", { class: "lbl" }, `新增 ${ui.n} ${kind === "likely" ? "題" : "項"}`), icon("chev")),
      h("span", { class: "faint", style: "padding:0 6px" }, "現有內容會保留"), h("span", { class: "sp" }),
      h("button", { class: "send", type: "button", "aria-label": "產生", disabled: !!ui.busy, onclick: () => generateMore(kind, ta.value.trim()) }, icon("up"))));
  }

  function draw() {
    if (!ctx.isCurrent()) return;
    const b = prep.basis;
    const pending = prep.status !== "ready";
    const analyzing = pending || ui.busy === "analyze" || prep.pending_generation?.kind === "analyze";
    const resumes = S.resumes || [];
    const basis = h("div", { class: "basis" },
      h("div", { class: "basis-h" }, icon("bulb"), "Ava 依據這兩份資料分析"),
      h("div", { class: "su-row" }, h("span", { class: "k" }, "職缺"), h("div", { class: "v" },
        h("button", { class: "picker", type: "button", onclick: () => (target.job.source === "user" ? location.hash = `#targets/${tid}` : openJob(target.job.id)) },
          h("b", null, b.job.company_name), h("span", null, `${b.job.title}・查看內容`), icon("right")))),
      h("div", { class: "su-row" }, h("span", { class: "k" }, "履歷"), h("div", { class: "v" }, resumes.length
        ? h("button", { class: "picker", type: "button", onclick: (e) => openMenu(e.currentTarget,
          [{ title: "用哪份履歷分析" }, ...resumes.map((r) => ({ label: r.filename, v: r.id, checked: b.resume?.id === r.id }))], async (v) => {
            if (v === b.resume?.id) return;
            try { await patch(`/targets/${tid}`, { resume_id: v }); toast("已切換履歷，Ava 正在重新分析"); ui.busy = "analyze"; await load(); draw(); watch(prep.pending_generation?.id, "analyze"); }
            catch (e) { errorToast(e); }
          }) }, icon("file"), h("b", null, b.resume?.filename || "選擇履歷"), icon("chev"))
        : [h("button", { class: "btn out sm", type: "button", onclick: () => openSettings("resume", { onChanged: async () => { await loadResumes(); await load(); draw(); } }) }, icon("upload"), "上傳履歷"),
          h("span", { class: "hint" }, "上傳後可以看到你的優勢與落差")])),
      h("div", { class: "basis-f" },
        h("span", { class: "faint" }, analyzing ? "Ava 正在讀取職缺內容和你的履歷⋯"
          : b.stale ? "職缺或履歷有更新，建議重新分析。" : `上次分析：${fmtDate(b.analyzed_at)}。職缺或履歷有更新時，重新分析一次。`),
        h("button", { class: "btn pri sm", type: "button", disabled: analyzing, onclick: analyze }, icon("redo"), analyzing ? "分析中⋯" : "重新分析")));

    const back = h("a", { class: "linkb", href: "#prep", style: "margin:0 0 18px -6px" }, icon("left", 14), "所有目標職缺");
    if (pending) {
      replace(wrap, back, h("h1", null, `${b.job.company_name} 面試建議`), basis,
        prep.status === "failed" ? h("div", { class: "err-box", style: "margin-top:20px" }, h("span", { class: "t" }, "Ava 這次沒有分析成功。"),
          h("button", { class: "btn out sm", type: "button", onclick: analyze }, "再試一次"))
          : [h("div", { class: "center-cta" }, dots(), h("p", { class: "muted" }, "Ava 正在比對職缺內容和你的履歷，通常不到 30 秒。")), skel(3)]);
      return;
    }
    const score = prep.match_score;
    replace(wrap, back,
      h("div", { class: "eyebrow" }, b.job.title),
      h("h1", null, `${b.job.company_name} 面試建議`), basis,
      score != null ? h("div", { class: "big" }, h("b", { class: "num" }, `${score}%`), h("span", null, `契合度・${prep.verdict_title || ""}`)) : null,
      prep.verdict_text ? h("p", { class: "muted", style: "margin-top:14px" }, prep.verdict_text) : null,
      h("div", { class: "grid2" },
        h("div", null, h("h2", null, "你的優勢"), prep.strengths.length ? h("ul", { class: "ticks" }, prep.strengths.map((t) => h("li", null, t)))
          : h("p", { class: "faint" }, "上傳履歷後可以看到你的優勢。")),
        h("div", null, h("h2", null, "需要補強"), h("ul", { class: "ticks warn" }, prep.gaps.map((t) => h("li", null, t))))),
      prep.directions.length ? [h("h2", null, "高機率會問的方向"),
        h("div", { class: "tbl-w" }, h("table", null, h("tbody", null, prep.directions.map((d) => h("tr", null, h("td", null, d.label),
          h("td", { style: "width:36%" }, h("div", { class: "bar" }, h("i", { style: `width:${d.probability}%` }))), h("td", { class: "n num" }, `${d.probability}%`)))))),
        h("p", { class: "faint", style: "margin-top:8px" }, "依職缺關鍵字、履歷落差與同類職缺常見題目估算。")] : null,

      h("div", { class: "sec-row" }, h("h2", null, "可能被問的題目"),
        h("button", { class: `more-btn${ui.more === "likely" ? " on" : ""}`, type: "button", onclick: () => { ui.more = ui.more === "likely" ? null : "likely"; draw(); } }, icon("plus"), "再多產生")),
      moreComposer("likely"),
      prep.likely_questions.map((l, i) => h("details", { class: "acc", open: l.is_new || i === 0 },
        h("summary", null, h("span", { class: "faint num" }, i + 1), h("span", { class: "t" }, l.question, l.is_new ? h("span", { class: "newtag" }, "新") : null),
          h("span", { class: "tagx" }, `${PRI[l.priority]}機率`), icon("chev")),
        h("div", { class: "bd" }, h("div", { class: "sub-h", style: "margin-top:0" }, "為什麼會問"), h("p", { class: "muted" }, l.why),
          h("div", { class: "codebox" }, h("div", { class: "cb-h" }, icon("bulb"), "回答建議"), h("div", { class: "cb-b" }, l.tip)),
          h("div", { class: "acc-act" }, l.in_set ? h("span", { class: "faint" }, icon("check", 14, { style: "display:inline;vertical-align:-2px;margin-right:4px" }), "已加入面試題庫")
            : h("button", { class: "btn out sm", type: "button", onclick: () => addToSet([l.id]) }, icon("plus"), "加入面試題庫，模擬面試時練"))))),
      ui.busy === "likely" ? skel(2) : null,
      prep.likely_questions.some((l) => !l.in_set) ? h("div", { style: "margin-top:12px" },
        h("button", { class: "linkb", type: "button", onclick: () => addToSet(null) }, icon("plus"), "全部加入面試題庫")) : null,

      h("div", { class: "sec-row" }, h("h2", null, "面試前準備清單"),
        h("button", { class: `more-btn${ui.more === "check" ? " on" : ""}`, type: "button", onclick: () => { ui.more = ui.more === "check" ? null : "check"; draw(); } }, icon("plus"), "再多產生")),
      moreComposer("check"),
      prep.checklist.map((c) => h("label", { class: "chk" },
        h("input", { type: "checkbox", checked: c.is_checked, onchange: async (e) => {
          c.is_checked = e.target.checked;
          try { await patch(`/preps/${prep.id}/checklist/${c.id}`, { is_checked: c.is_checked }); }
          catch (err) { c.is_checked = !c.is_checked; e.target.checked = c.is_checked; errorToast(err); }
        } }),
        h("span", null, c.text, c.is_new ? h("span", { class: "newtag" }, "新") : null))),
      ui.busy === "check" ? skel(2, "height:40px") : null,
      h("div", { class: "row", style: "margin-top:28px" }, h("a", { class: "btn pri", href: "#new" }, icon("mic"), "開始模擬面試")));
  }

  draw();
  if (prep.status !== "ready" || prep.pending_generation) {
    ui.busy = prep.pending_generation?.kind === "likely_more" ? "likely" : prep.pending_generation?.kind === "checklist_more" ? "check" : prep.pending_generation ? "analyze" : null;
    watch(prep.pending_generation?.id, prep.pending_generation?.kind || "analyze");
  }
  return () => clearTimeout(poll);
}
