// 我的職缺：列表 → 職缺頁（分頁：總覽／題庫／面試建議／報告）。這裡負責「總覽」與「報告」分頁；
// 總覽＝下一步、職缺內容、編輯原文與重新出題、移出。題庫在 questions.js，面試建議在 prep.js。
import { del, get, patch, post } from "../api.js";
import { addTargetMenuItems, onAddTargetPick, openJd } from "../components/jd.js";
import { jobType, sourceButton, sourceLine } from "../components/job.js";
import { jobHead } from "../components/jobhead.js";
import { reportRow } from "./report.js";
import { dots, h, icon, replace } from "../dom.js";
import { renderSide } from "../shell.js";
import { feature, fmtDate, loadMe, loadTargets, pickTarget, S } from "../store.js";
import { setLeaveGuard } from "../router.js";
import { closeModal, confirmBox, copyText, errorToast, logo, openMenu, openModal, setHead, toast } from "../ui.js";

export async function render(view, tid, ctx) {
  return tid ? detail(view, tid, ctx) : list(view, ctx);
}

async function list(view, ctx) {
  setHead("target", "我的職缺");
  const wrap = h("div", { class: "wrap doc" });
  view.append(wrap);
  await loadTargets().catch(() => {});
  if (!ctx.isCurrent()) return;
  const tg = S.targets || [];
  replace(wrap, h("h1", null, "我的職缺"),
    h("p", { class: "muted", style: "margin-top:6px" }, "你正在準備的職缺。每個職缺都有自己的題庫、面試建議和面試報告。"),
    h("div", { style: "margin-top:20px" },
      tg.map((t) => h("a", { class: "hrow", href: `#targets/${t.id}` }, logo(t.job.company_name.slice(0, 1).toUpperCase()),
        h("span", { class: "bd" }, h("b", null, `${t.job.company_name}・${t.job.title}`),
          h("span", { class: "m" }, [t.match_score != null ? `契合度 ${t.match_score}%` : null,
            t.question_set.status === "preparing" ? "Ava 出題中⋯" : `${t.question_set.item_count} 題`,
            t.last_report ? `最近練習 ${fmtDate(t.last_report.ended_at)}・${t.last_report.overall_score} 分` : "還沒練習"].filter(Boolean).join("・"))),
        h("span", { class: "ch" }, icon("right")))),
      tg.length ? h("button", { class: "addrow", type: "button", onclick: (e) => openMenu(e.currentTarget, addTargetMenuItems(), onAddTargetPick) }, icon("plus"), "新增職缺")
        : h("div", { class: "empty-note" }, h("p", null, feature("job_search") ? "還沒有職缺。貼上你想應徵的職缺，或到「職缺搜尋」挑一個。" : "還沒有職缺。貼上你想應徵的職缺就能開始準備。"),
          h("button", { class: "btn pri", type: "button", onclick: () => openJd() }, icon("plus"), "貼上職缺內容"))));
}

async function detail(view, tid, ctx) {
  setHead("target", "我的職缺");
  const wrap = h("div", { class: "wrap doc" }, h("div", { class: "center-load" }, dots()));
  view.append(wrap);
  let t, editing = false, saving = false, draft = "", timer = null, rawOpen = false;
  try { t = await get(`/targets/${tid}`); } catch {
    if (ctx.isCurrent()) replace(wrap, h("h1", null, "找不到這個職缺"), h("a", { class: "linkb", href: "#targets" }, "回到我的職缺"));
    return;
  }
  if (!ctx.isCurrent()) return;
  pickTarget(tid);
  setHead("target", t.job.company_name);
  if (ctx.sub === "reports") return reportsTab(wrap, t, ctx);
  setLeaveGuard(() => editing && draft.trim() !== (t.job.raw_text || "").trim());     // 原文改到一半：離開前確認

  // 重新出題後：Ava 解析與出題期間每 4 秒更新一次（編輯原文時不打斷）
  const busy = () => t.question_set.status === "preparing" || (t.job.source === "user" && ["pending", "parsing"].includes(t.job.parse_status));
  const insightBusy = () => ["pending", "running"].includes(t.job.insight?.status);
  const poll = () => {
    clearTimeout(timer);
    if (!busy() && !insightBusy()) return;
    timer = setTimeout(async () => {
      if (!ctx.isCurrent()) return;
      if (editing) { poll(); return; }
      try { t = await get(`/targets/${tid}`); draw(); } catch { /* 下一輪再試 */ }
      poll();
    }, 4000);
  };

  async function saveRaw() {
    const text = draft.trim();
    if (text.length < 20) { toast("內容太短，至少要有職稱和工作內容"); return; }
    if (text === t.job.raw_text) { editing = false; draw(); return; }
    saving = true; draw();
    try {
      t = await patch(`/targets/${tid}`, { raw_text: text });
      editing = false;
      toast("已儲存原始職缺內容，題庫沒有變動");
    } catch (e) { errorToast(e); }
    saving = false; draw();
  }

  async function regenerate() {
    const ok = await confirmBox({
      title: "依新內容重新出題？",
      text: `Ava 會依目前的原始職缺內容重新整理職缺資訊，重新出 8 題並換掉 Ava 之前出的題目（你自己新增的題目會保留），面試建議也會重新分析。過去的面試報告不受影響。`,
      ok: "重新出題",
    });
    if (!ok) return;
    try {
      t = await post(`/targets/${tid}/regenerate`);
      await loadTargets().catch(() => {});
      renderSide();
      toast("Ava 開始依新內容重新出題");
      draw(); poll();
    } catch (e) { errorToast(e); }
  }

  /** 新增／修改職缺網址：只存連結，不會重新出題。 */
  function editLink() {
    const inp = h("input", { class: "inp", type: "url", maxlength: 1000, "aria-label": "職缺網址",
      placeholder: "https://www.104.com.tw/job/…", value: t.job.source_url || "" });
    const err = h("div", { class: "form-err", role: "alert", style: "margin-top:8px" });
    const save = async (v) => {
      if (v && !/^https?:\/\/\S+$/i.test(v)) { err.textContent = "職缺網址要以 http:// 或 https:// 開頭"; return; }
      try {
        t = await patch(`/targets/${tid}`, { source_url: v });
        closeModal(); draw();
        toast(v ? "已儲存職缺網址" : "已移除職缺網址");
      } catch (e) { err.textContent = e.message; }
    };
    openModal({ title: t.job.source_url ? "修改職缺網址" : "加上職缺網址", cls: "sm", body: [
      h("p", { class: "muted", style: "margin-bottom:12px" }, "貼上這個職缺在 104、CakeResume 或公司官網的網址，之後可以從這裡直接打開。只會儲存連結，不會重新出題。"),
      inp, err,
      h("div", { class: "row", style: "justify-content:flex-end;margin-top:14px" },
        t.job.source_url ? h("button", { class: "btn ghost", type: "button", style: "margin-right:auto", onclick: () => save("") }, "移除網址") : null,
        h("button", { class: "btn ghost", type: "button", onclick: closeModal }, "取消"),
        h("button", { class: "btn pri", type: "button", onclick: () => save(inp.value.trim()) }, "儲存")),
    ] });
    inp.focus();
  }

  /** 原文已修改、還沒重新出題時的提示。 */
  const outdatedNote = () => t.jd_outdated ? h("div", { class: "err-box", role: "status", style: "margin-top:20px" },
    h("div", { class: "t" }, h("b", null, "原始職缺內容已修改"),
      h("div", { class: "muted" }, "題庫和面試建議還是依照修改前的內容產生的。想讓 Ava 依新內容出題，再按右邊的按鈕。")),
    h("button", { class: "btn pri sm", type: "button", disabled: busy(), onclick: regenerate }, icon("redo"), "依新內容重新出題")) : null;

  // 使用者自己貼的職缺：可以展開看、編輯當初貼上的原文（平台職缺的原文只給 AI 用，API 不會回傳）
  function rawSection() {
    const j = t.job;
    if (j.source !== "user" || !j.raw_text) return null;
    const body = editing
      ? [h("textarea", { class: "inp", rows: 14, maxlength: 20000, style: "min-height:280px", "aria-label": "原始職缺內容",
            disabled: saving, oninput: (e) => { draft = e.target.value; } }, draft),
         h("p", { class: "faint", style: "margin-top:6px" }, "儲存只會更新文字，不會重新出題。"),
         h("div", { class: "row", style: "margin-top:10px" },
           h("button", { class: "btn pri sm", type: "button", disabled: saving, onclick: saveRaw }, saving ? "儲存中⋯" : "儲存"),
           h("button", { class: "btn out sm", type: "button", disabled: saving, onclick: () => { editing = false; draw(); } }, "取消"))]
      : [h("div", { class: "raw-text" }, j.raw_text),
         h("div", { class: "row", style: "margin-top:10px" },
           h("button", { class: "btn out sm", type: "button", onclick: () => { editing = true; draft = j.raw_text; draw(); } }, icon("pencil"), "編輯"),
           h("button", { class: "btn out sm", type: "button", onclick: () => copyText(j.raw_text, "已複製原始內容") },
             icon("copy"), "複製全文"),
           h("button", { class: "btn out sm", type: "button", disabled: busy(), title: busy() ? "Ava 正在出題" : "",
             onclick: regenerate }, icon("redo"), "依新內容重新出題"))];
    return h("details", { class: "acc raw-jd", id: "rawJd", open: rawOpen || editing, ontoggle: (e) => { rawOpen = e.target.open; } },
      h("summary", null, icon("file"), h("span", { class: "t" }, "你貼上的原始職缺內容"),
        h("span", { class: "faint" }, `${j.raw_text.length} 字`), icon("chev")),
      h("div", { class: "bd" }, body));
  }

  async function retryInsight() {
    try { t = await post(`/targets/${tid}/insight`); draw(); poll(); } catch (e) { errorToast(e); }
  }

  /** Ava 的職缺解讀：白話說明這個職缺在做什麼、想找什麼人、要拿哪些經驗證明，讓使用者知道要準備哪些故事。 */
  function insightSection() {
    const ins = t.job.insight || { status: "pending" };
    const head = h("h2", null, "Ava 怎麼看這個職缺");
    if (ins.status === "failed") return [head, h("div", { class: "err-box", role: "status" },
      h("div", { class: "t" }, h("b", null, "Ava 沒有解讀成功"), h("div", { class: "muted" }, "可能是 AI 服務暫時忙碌。題庫和面試建議不受影響。")),
      h("button", { class: "btn out sm", type: "button", onclick: retryInsight }, icon("redo"), "再試一次"))];
    if (ins.status !== "ready" || !ins.data) return [head, h("p", { class: "muted row", style: "gap:8px" }, dots(), "Ava 正在讀這個職缺，整理公司實際想找什麼樣的人⋯")];
    const d = ins.data;
    const must = d.experiences.filter((e) => e.kind === "must"), plus = d.experiences.filter((e) => e.kind !== "must");
    const expList = (title, xs) => xs.length ? h("div", null, h("div", { class: "sub-h" }, title),
      h("ul", { class: "ins-exp" }, xs.map((e) => h("li", null, e.text, h("span", { class: "faint" }, e.proof))))) : null;
    return [head,
      h("p", { class: "ins-essence" }, d.essence),
      d.persona.length ? [h("div", { class: "sub-h" }, "他們想找的人"), h("ul", null, d.persona.map((x) => h("li", null, x)))] : null,
      h("div", { class: "sub-h" }, "這份工作的重點"),
      h("div", { class: "ins-focus" }, d.focus.map((f, i) => h("section", { class: "ins-card" },
        h("h3", null, h("span", { class: "num" }, i + 1), f.title),
        f.quote ? h("blockquote", null, f.quote) : null,
        h("p", null, f.meaning),
        f.evidence.length ? [h("div", { class: "lbl" }, "面試官想聽到你做過"), h("ul", null, f.evidence.map((x) => h("li", null, x)))] : null,
        f.story ? h("div", { class: "ins-story" }, icon("bulb", 16), h("span", null, h("b", null, "準備這個故事："), f.story)) : null))),
      must.length || plus.length ? h("div", { class: "grid2" }, expList("必備經驗", must), expList("加分經驗", plus)) : null,
      d.signals.length ? [h("div", { class: "sub-h" }, "字裡行間"), h("ul", null, d.signals.map((x) => h("li", null, x)))] : null,
      h("p", { class: "faint", style: "margin-top:14px" }, "Ava 依職缺內容推論，僅供準備參考。")];
  }

  /** 總覽的「下一步」：只講狀態和接下來要做什麼（入口在上面的分頁）。 */
  function nextBox() {
    const lr = t.last_report, p = t.prep;
    const nx = (k, ...v) => h("div", { class: "nx" }, h("span", { class: "k" }, k), h("span", { class: "v" }, v));
    const practice = t.question_set.status === "preparing" ? nx("練習", "Ava 正在出題，好了就能開始練習。")
      : lr ? nx("練習", `最近一次 ${lr.overall_score} 分（${fmtDate(lr.ended_at)}）。`, h("a", { class: "linkb", href: `#report/${lr.session_id}` }, "看這份報告", icon("right", 14)))
        : nx("練習", "還沒練習過。按「練習這個職缺」練一場，結束後會有逐題評分和改善建議。");
    const prep = p.status !== "ready" ? nx("準備", "Ava 正在整理面試建議⋯")
      : p.checklist_total && p.checklist_done < p.checklist_total
        ? nx("準備", `面試前準備清單完成 ${p.checklist_done}/${p.checklist_total}。`, h("a", { class: "linkb", href: `#prep/${tid}` }, "看面試建議", icon("right", 14)))
        : nx("準備", `${p.likely_count} 個可能被問的題目，準備清單都完成了。`, h("a", { class: "linkb", href: `#prep/${tid}` }, "看面試建議", icon("right", 14)));
    return h("div", { class: "nextbox" }, h("div", { class: "sub-h", style: "margin:0" }, "下一步"), practice, prep);
  }

  function draw() {
    if (!ctx.isCurrent()) return;
    const j = t.job;
    const raw = rawSection();
    const smooth = !matchMedia("(prefers-reduced-motion: reduce)").matches;
    const showRaw = () => { raw.open = true; raw.scrollIntoView({ behavior: smooth ? "smooth" : "auto", block: "start" }); };
    const tags = [t.match_score != null ? h("span", { class: "tagx" }, `${t.match_score}% 契合`) : null,
      j.source !== "user" ? h("span", { class: "tagx" }, jobType(j)) : null, j.salary_text ? h("span", { class: "tagx" }, j.salary_text) : null].filter(Boolean);
    const extra = [tags.length ? h("div", { class: "row", style: "margin-top:8px" }, tags) : null,
      raw ? h("div", { class: "row faint", style: "margin:6px 0 0 -6px;gap:2px 10px" },
        h("button", { class: "linkb", type: "button", style: "text-decoration:underline", onclick: showRaw }, "查看或編輯原文"),
        h("button", { class: "linkb", type: "button", style: "text-decoration:underline", onclick: editLink },
          j.source_url ? "修改職缺網址" : "加上職缺網址"))
        : h("div", { style: "margin-top:6px" }, sourceLine(j), sourceButton(j))];
    replace(wrap,
      jobHead(t, "overview", { extra }),
      outdatedNote(),
      nextBox(),
      h("h2", null, "關於這個職缺"),
      j.parse_status !== "parsed" && j.source === "user" ? h("p", { class: "muted" }, j.parse_status === "failed" ? "Ava 沒有解析成功，題目會依你貼上的原文出。" : "Ava 正在整理職缺內容⋯")
        : h("p", { class: "muted" }, j.summary || j.about || ""),
      j.parse_status !== "parsed" && j.source === "user" ? null : insightSection(),
      h("h2", null, "職缺列出的內容"),
      h("div", { class: "grid2" },
        h("div", null, h("div", { class: "sub-h" }, "工作內容"), h("ul", null, (j.duties || []).map((d) => h("li", null, d)))),
        h("div", null, h("div", { class: "sub-h" }, "條件要求"), h("ul", null, (j.requirements || []).map((d) => h("li", null, d))))),
      j.tags.length ? h("div", { class: "row", style: "margin-top:16px" }, j.tags.map((x) => h("span", { class: "tagx" }, x))) : null,
      raw,
      h("div", { style: "text-align:center;margin-top:36px" }, h("button", { class: "linkb", type: "button", onclick: async () => {
        const ok = await confirmBox({ title: "從我的職缺移出？", text: "題庫、面試建議和報告都會保留，之後重新加入就能找回。", ok: "移出" });
        if (!ok) return;
        try {
          await del(`/targets/${tid}`);
          await Promise.all([loadTargets(), loadMe()]);
          renderSide();
          location.hash = "#targets";
          toast("已從我的職缺移出");
        } catch (e) { errorToast(e); }
      } }, "從我的職缺移出")));
  }

  draw();
  poll();
  return () => clearTimeout(timer);
}

/** 「報告」分頁：只列這個職缺的面試報告。 */
async function reportsTab(wrap, t, ctx) {
  const tid = t.id;
  replace(wrap, jobHead(t, "reports"), h("div", { class: "center-load" }, dots()));
  let poll = null;
  const load = async () => {
    let data;
    try { data = await get(`/interviews?limit=50&target_id=${tid}`); } catch (e) { errorToast(e); return; }
    if (!ctx.isCurrent()) return;
    const sm = data.summary;
    replace(wrap, jobHead(t, "reports"), data.items.length
      ? [h("p", { class: "muted" }, `練習過 ${sm.total} 場・平均 ${sm.avg_score ?? "-"} 分・最高 ${sm.best_score ?? "-"} 分。`),
        h("div", { style: "margin-top:8px" }, data.items.map((x) => reportRow(x, { showJob: false })))]
      : h("div", { class: "empty-note" }, h("p", null, "這個職缺還沒有面試報告。練一場之後，逐題評分和改善建議會出現在這裡。"),
        h("a", { class: "btn pri", href: "#new", onclick: () => pickTarget(tid) }, icon("mic"), "練習這個職缺")));
    clearTimeout(poll);
    if (data.items.some((x) => x.report_status !== "ready")) poll = setTimeout(() => ctx.isCurrent() && load(), 4000);
  };
  await load();
  return () => clearTimeout(poll);
}
