// 面試報告：列表（最近 7 天／更早）→ 詳情（總分、五面向、逐題回顧：主題目＋追問、整組分數）。
import { del, get, post, sse } from "../api.js";
import { dots, h, highlight, icon, replace, skel } from "../dom.js";
import { playUrl } from "../interview/player.js";
import { renderSide } from "../shell.js";
import { fmtDate, loadMe, PERSONAS, pickTarget } from "../store.js";
import { backLink, confirmBox, errorToast, openMenu, setHead, toast } from "../ui.js";

export async function render(view, sid, ctx) {
  return sid ? detail(view, sid, ctx) : list(view, ctx);
}

const durText = (sec) => (sec ? `${Math.max(1, Math.round(sec / 60))} 分鐘` : "");

async function list(view, ctx) {
  setHead("report", "面試報告");
  const wrap = h("div", { class: "wrap doc" }, h("h1", null, "面試報告"), h("div", { class: "center-load" }, dots()));
  view.append(wrap);
  let data;
  try { data = await get("/interviews?limit=50"); } catch (e) { errorToast(e); return; }
  if (!ctx.isCurrent()) return;
  if (!data.items.length) {
    replace(wrap, h("h1", null, "面試報告"), h("div", { class: "empty-note" },
      h("p", null, "還沒有面試紀錄。完成第一場面試後，逐題評分和改善建議會出現在這裡。"),
      h("a", { class: "btn pri", href: "#new" }, icon("mic"), "開始第一場面試")));
    return;
  }
  const box = h("div", { id: "hlist", style: "margin-top:8px" });
  const drawList = (items) => {
    if (!items.length) { replace(box, h("p", { class: "muted", style: "padding:20px 12px" }, "找不到符合的面試報告。")); return; }
    const weekAgo = Date.now() - 7 * 86400000;
    const groups = [["最近 7 天", items.filter((x) => new Date(x.ended_at) >= weekAgo)], ["更早", items.filter((x) => new Date(x.ended_at) < weekAgo)]];
    replace(box, groups.filter(([, xs]) => xs.length).map(([g, xs]) => [h("div", { class: "grp-h" }, g), xs.map((x) =>
      h("a", { class: "hrow", href: `#report/${x.id}` },
        h("span", { class: "bd" }, h("b", null, x.job_label),
          h("span", { class: "m" }, `${fmtDate(x.ended_at)}・${x.answered_count} 題・${PERSONAS[x.persona]?.name || ""}${x.duration_sec ? `・${durText(x.duration_sec)}` : ""}`)),
        h("span", { class: "hs" }, x.report_status === "ready"
          ? [h("b", { class: "num" }, x.overall_score ?? "-"), h("small", null, x.delta_vs_prev != null ? `${x.delta_vs_prev >= 0 ? "+" : ""}${x.delta_vs_prev}` : "首次")]
          : h("span", { class: "proc" }, dots(), "評分中")),
        h("span", { class: "ch" }, icon("right"))))]));
  };
  let timer = null;
  const input = h("input", { id: "hq", placeholder: "搜尋職缺或公司", "aria-label": "搜尋面試報告", oninput: () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const q = input.value.trim();
      try { const r = await get(`/interviews?limit=50${q ? `&q=${encodeURIComponent(q)}` : ""}`); if (ctx.isCurrent()) drawList(r.items); } catch { /* ignore */ }
    }, 250);
  } });
  const sm = data.summary;
  replace(wrap, h("h1", null, "面試報告"),
    h("p", { class: "muted", style: "margin-top:6px" }, `共 ${sm.total} 場・平均 ${sm.avg_score ?? "-"} 分・最高 ${sm.best_score ?? "-"} 分。點一場面試，看逐題評分與改善建議。`),
    h("label", { class: "search", style: "margin-top:20px" }, icon("search"), input), box);
  drawList(data.items);
  // 有評分中的報告：輕量輪詢，完成時更新
  if (data.items.some((x) => x.report_status !== "ready")) {
    const poll = setInterval(async () => {
      if (!ctx.isCurrent()) return clearInterval(poll);
      try {
        const r = await get("/interviews?limit=50");
        if (!r.items.some((x) => x.report_status !== "ready")) { clearInterval(poll); toast("報告完成了"); }
        if (ctx.isCurrent() && !input.value) drawList(r.items);
      } catch { /* ignore */ }
    }, 4000);
    return () => clearInterval(poll);
  }
}

function answerBlock(sid, ans) {
  if (!ans) return null;
  return [
    h("div", { class: "sub-h", style: "display:flex;align-items:center;gap:8px" }, "你的回答",
      ans.has_audio ? h("button", { class: "linkb", type: "button", style: "font-weight:400", onclick: async () => {
        try { const r = await get(`/interviews/${sid}/answers/${ans.attempt_id}/audio`); await playUrl(r.url); } catch (e) { errorToast(e); }
      } }, icon("speak", 14), "聽我的錄音") : null),
  ];
}

function evalBlock(ev, transcript, { copyable = true, onRetry } = {}) {
  const out = [h("p", { class: "muted", style: "margin-top:6px" }, transcript ? highlight(transcript, ev?.highlight) : "（沒有轉錄內容）")];
  if (!ev || ev.status === "pending" || ev.status === "running") { out.push(h("p", { class: "faint", style: "margin-top:10px;display:flex;gap:8px;align-items:center" }, dots(), "Ava 正在評分這題⋯")); return out; }
  if (ev.status === "needs_review" || ev.status === "failed") {
    out.push(h("div", { class: "err-box", style: "margin-top:12px" }, h("span", { class: "t" }, ev.review_reason || "這題評分失敗"),
      onRetry ? h("button", { class: "btn out sm", type: "button", onclick: onRetry }, icon("redo"), "重新評分") : null));
    return out;
  }
  out.push(h("div", { class: "grid2" },
    h("div", null, h("div", { class: "sub-h" }, "做得好的地方"), h("ul", { class: "ticks" }, ev.strengths.map((x) => h("li", null, x)))),
    h("div", null, h("div", { class: "sub-h" }, "改善建議"), h("ul", { class: "ticks warn" }, ev.improvements.map((x) => h("li", null, x))))));
  if (ev.better_answer) {
    out.push(h("div", { class: "codebox" }, h("div", { class: "cb-h" }, icon("speak"), "可以這樣說", h("span", { class: "sp" }),
      copyable ? h("button", { class: "ib sm", type: "button", "aria-label": "複製建議說法", onclick: () => { navigator.clipboard?.writeText(ev.better_answer); toast("已複製"); } }, icon("copy")) : null),
    h("div", { class: "cb-b" }, ev.better_answer)));
  }
  return out;
}

async function detail(view, sid, ctx) {
  setHead("report", "面試報告");
  const wrap = h("div", { class: "wrap doc" }, backLink("#report", "所有面試報告"), h("div", { class: "center-load" }, dots()));
  view.append(wrap);
  let stop = null;

  const load = async () => {
    let r;
    try { r = await get(`/interviews/${sid}/report`); } catch (e) {
      if (!ctx.isCurrent()) return;
      replace(wrap, backLink("#report", "所有面試報告"), h("h1", null, "找不到這份報告"),
        h("p", { class: "muted", style: "margin-top:8px" }, e.status === 404 ? "這份報告可能已經刪除。" : e.message));
      return;
    }
    if (!ctx.isCurrent()) return;
    draw(r);
    if (r.status !== "ready" && !stop) {
      stop = sse(`/interviews/${sid}/report/events`, (ev) => {
        if (ev === "evaluation_done" || ev === "report_ready") load();
        if (ev === "report_ready") { toast("報告完成了"); stop?.(); stop = null; }
      }, (err) => { if (err && ctx.isCurrent()) setTimeout(() => { stop = null; load(); }, 3000); });
    }
  };

  const draw = (r) => {
    const s = r.session;
    const meta = `${fmtDate(s.ended_at)}・${r.items.filter((i) => i.status === "answered").length} 題・${PERSONAS[s.persona]?.name || ""}${s.duration_sec ? `・${durText(s.duration_sec)}` : ""}`;
    setHead("report", "面試報告", null, [h("button", { class: "ib", type: "button", "aria-label": "更多", onclick: (e) => openMenu(e.currentTarget,
      [{ icon: "trash", label: "刪除這份報告", sub: "連同錄音一起刪除", v: "del" }], async () => {
        if (!(await confirmBox({ title: "刪除這份報告？", text: "報告、逐字稿和錄音都會刪除，無法復原。", ok: "刪除", danger: true }))) return;
        try { await del(`/interviews/${sid}`); await loadMe(); renderSide(); location.hash = "#report"; toast("已刪除"); } catch (e) { errorToast(e); }
      }) }, icon("more"))]);
    if (r.status !== "ready") {
      replace(wrap, backLink("#report", "所有面試報告"), h("div", { class: "eyebrow" }, meta), h("h1", null, s.job_label),
        h("p", { class: "muted", style: "margin-top:14px;display:flex;align-items:center;gap:10px" }, dots(),
          `Ava 正在逐題轉錄和評分（${r.progress.evaluated}/${r.progress.total}），通常不到一分鐘。完成後會通知你，可以先去做別的事。`),
        h("div", { style: "margin-top:24px" }, skel(Math.min(5, r.items.length + 2))));
      return;
    }
    const retry = (qid) => async () => {
      try { await post(`/interviews/${sid}/questions/${qid}/re-evaluate`); toast("已重新排入評分"); load(); } catch (e) { errorToast(e); }
    };
    const m = r.metrics;
    replace(wrap, backLink("#report", "所有面試報告"),
      h("div", { class: "eyebrow" }, meta), h("h1", null, s.job_label),
      h("div", { class: "big" }, h("b", { class: "num" }, r.overall_score ?? "-"),
        h("span", null, `/ 100${r.delta_vs_prev != null ? `・比上次 ${r.delta_vs_prev >= 0 ? "+" : ""}${r.delta_vs_prev} 分` : "・第一次練習"}`)),
      r.summary ? h("p", { class: "muted", style: "margin-top:16px" }, r.summary) : null,
      h("div", { class: "row", style: "margin-top:18px" },
        m.filler_count != null ? h("span", { class: "pill" }, `贅詞 ${m.filler_count} 次`) : null,
        m.avg_answer_sec ? h("span", { class: "pill" }, `平均每題 ${Math.floor(m.avg_answer_sec / 60)}:${String(m.avg_answer_sec % 60).padStart(2, "0")}`) : null,
        m.speech_rate ? h("span", { class: "pill" }, `語速 ${Math.round(m.speech_rate)} ${m.speech_rate_unit === "words_per_min" ? "詞" : "字"}/分`) : null),
      r.focus_areas.length ? [h("h2", null, "下次練習的重點"), h("ul", null, r.focus_areas.map((x) => h("li", null, x)))] : null,
      h("h2", null, "五個面向"),
      h("div", { class: "tbl-w" }, h("table", null,
        h("thead", null, h("tr", null, h("th", null, "面向"), h("th", { class: "hide-m" }, "看什麼"), h("th", { style: "width:36%" }, "分數"), h("th", { class: "n" }, "分"))),
        h("tbody", null, r.dimensions.map((d) => h("tr", null, h("td", null, d.label), h("td", { class: "muted hide-m" }, d.hint),
          h("td", null, h("div", { class: "bar" }, h("i", { style: `width:${d.score ?? 0}%` }))), h("td", { class: "n num" }, d.score ?? "-")))))),
      h("h2", null, "逐題回顧"),
      r.items.map((it) => {
        const fus = it.followups.filter((f) => f.status !== "pending" || f.answer);
        const review = it.evaluation && ["needs_review", "failed"].includes(it.evaluation.status);
        const score = it.group?.score ?? (review ? "需複查" : it.evaluation?.score);
        return h("section", { class: "qsec" },
          h("div", { class: "qh" }, h("h3", null, `${it.order_no}. ${it.category}`,
            fus.length ? h("span", { class: "grp" }, `主題目＋${fus.length} 個追問・整組分數`) : null),
          h("span", { class: "sc num" }, it.status === "answered" ? score ?? "-" : it.status === "skipped" ? "已跳過" : "未作答")),
          fus.length && it.evaluation?.status === "done" ? h("div", { class: "sub-h", style: "margin-top:6px" }, `主題目・${it.evaluation.score} 分`) : null,
          h("blockquote", null, it.question_text),
          it.status === "answered" ? [answerBlock(sid, it.answer), evalBlock(it.evaluation, it.answer?.transcript, { onRetry: retry(it.question_id) })] : null,
          fus.map((f) => h("div", { class: "fu-sec" },
            h("div", { class: "qh" }, h("h3", null, `追問 ${f.followup_no}`),
              h("span", { class: "sc num" }, f.status === "skipped" ? "已跳過" : ["needs_review", "failed"].includes(f.evaluation?.status) ? "需複查" : f.evaluation?.score ?? "-")),
            h("blockquote", null, f.question_text),
            f.status === "answered" ? [answerBlock(sid, f.answer), evalBlock(f.evaluation, f.answer?.transcript, { copyable: false, onRetry: retry(f.question_id) })] : null)));
      }),
      h("div", { class: "row", style: "margin-top:28px" },
        h("button", { class: "btn pri", type: "button", onclick: () => { pickTarget(s.target_id); location.hash = "#new"; } }, icon("redo"), "再練一次"),
        h("button", { class: "btn out", type: "button", onclick: async () => {
          try {
            const x = await post(`/interviews/${sid}/report/add-to-set`, {});
            toast(x.added ? `${x.added} 題已加入 ${s.company_name} 面試題庫最前面，下次面試會優先練` : "這些題目已經在面試題庫裡，已移到最前面");
          } catch (e) { errorToast(e); }
        } }, icon("plus"), "弱項加入面試題庫")));
  };

  await load();
  return () => stop?.();
}
