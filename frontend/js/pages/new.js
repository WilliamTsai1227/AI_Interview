// 新面試：選目標職缺、履歷、長度／風格／語言 → 開始面試（先取得麥克風權限，再建立場次）。
import { get, post } from "../api.js";
import { addTargetMenuItems, onAddTargetPick, openJd } from "../components/jd.js";
import { openSettings } from "../components/settings.js";
import { h, icon, replace, skel } from "../dom.js";
import { requestMic } from "../interview/recorder.js";
import { renderSide } from "../shell.js";
import { DIFF, EST, fuExpected, LANGS, loadMe, loadResumes, loadTargets, MODES, PERSONAS, pickTarget, prefs, S } from "../store.js";
import { confirmBox, errorToast, openMenu, seg, setHead, toast } from "../ui.js";

export async function render(view, _arg, ctx) {
  setHead(null, "新面試");
  const p = prefs();
  const st = { mode: p.default_length, persona: p.default_persona, lang: p.default_language, questions: null, busy: false };
  let poll = null;
  const root = h("div", { class: "setup-wrap" });
  view.append(root);
  try { await loadResumes(); } catch { S.resumes = []; }

  const targets = () => S.targets || [];
  const current = () => targets().find((t) => t.id === S.job) || targets()[0];

  async function loadQuestions() {
    const t = current();
    if (!t) { st.questions = []; return; }
    try { st.questions = (await get(`/targets/${t.id}/questions`)).items; } catch { st.questions = []; }
  }

  function schedulePoll() {
    clearTimeout(poll);
    const t = current();
    if (t && t.question_set.status === "preparing") {
      poll = setTimeout(async () => {
        if (!ctx.isCurrent()) return;
        await loadTargets();
        await loadQuestions();
        if (ctx.isCurrent()) { draw(); schedulePoll(); }
      }, 2000);
    }
  }

  function draw() {
    const tg = targets();
    const has = tg.length > 0;
    const t = current();
    if (t) pickTarget(t.id);
    const n = MODES[st.mode].n;
    const preparing = t && t.question_set.status === "preparing";
    const list = (st.questions || []).slice(0, n);
    const short = n - list.length;
    const mins = Math.round(list.reduce((a, q) => a + (EST[q.difficulty] || 3) + fuExpected(q.followup) * 2, 0) + short * 3);
    const resumes = S.resumes || [];
    const resumeSel = S.resumeSel !== undefined ? resumes.find((r) => r.id === S.resumeSel) || null
      : resumes.find((r) => r.id === t?.resume?.id) || resumes.find((r) => r.is_primary) || null;
    const act = S.me?.active_interview;

    const jobRow = h("div", { class: "su-row" }, h("span", { class: "k" }, "職缺"), h("div", { class: "v" }, has
      ? h("button", { class: "picker", type: "button", onclick: (e) => openMenu(e.currentTarget, [
        { title: "目標職缺" },
        ...tg.map((x) => ({ label: x.job.company_name, sub: `${x.job.title}・${x.question_set.status === "preparing" ? "出題中" : `${x.question_set.item_count} 題`}`, v: x.id, checked: x.id === t.id })),
        { sep: true }, ...addTargetMenuItems()], async (v) => {
        if (v === "jd" || v === "jobs") return onAddTargetPick(v);
        pickTarget(v); S.resumeSel = undefined; st.questions = null; draw(); await loadQuestions(); draw(); schedulePoll();
      }) }, h("b", null, t.job.company_name), h("span", null, t.job.title), icon("chev"))
      : [h("button", { class: "btn pri sm", type: "button", onclick: () => openJd() }, icon("plus"), "貼上職缺內容"),
        h("a", { class: "linkb", href: "#jobs" }, "或從職缺庫挑選")]));

    const resumeRow = h("div", { class: "su-row" }, h("span", { class: "k" }, "履歷"), h("div", { class: "v" }, resumes.length
      ? h("button", { class: "picker", type: "button", onclick: (e) => openMenu(e.currentTarget, [
        { title: "使用的履歷" },
        ...resumes.map((r) => ({ label: r.filename, sub: r.parse_status === "parsed" ? null : "解析中", v: r.id, checked: resumeSel?.id === r.id })),
        { label: "不使用履歷", sub: "題目只依職缺出", v: "_none", checked: !resumeSel },
        { sep: true }, { icon: "upload", label: "上傳新履歷", v: "_up" }], (v) => {
        if (v === "_up") openSettings("resume", { onChanged: refresh });
        else { S.resumeSel = v === "_none" ? null : v; draw(); }
      }) }, icon("file"), h("b", null, resumeSel ? resumeSel.filename : "不使用履歷"), icon("chev"))
      : [h("button", { class: "btn out sm", type: "button", onclick: () => openSettings("resume", { onChanged: refresh }) }, icon("upload"), "上傳履歷"),
        h("span", { class: "hint" }, "選填。沒有履歷也能練，題目會只依職缺出")]));

    const preview = h("div", { class: "qprev" },
      h("div", { class: "qprev-h" }, h("span", null, `本次題目（${n} 題）`),
        h("a", { href: `#questions/${t?.id}` }, "編輯面試題庫", icon("right", 14))),
      preparing || st.questions === null
        ? [...skel(2, "height:20px;margin:10px 0"), h("div", { class: "faint" }, "Ava 正在依職缺和履歷出題⋯")]
        : h("ol", null, list.map((q, i) => {
          const f = q.followup;
          const ft = f.mode === "fixed" ? `＋追問 ${f.count}` : f.mode === "planned" ? `＋追問 ${f.planned.length}` : f.mode === "adaptive" ? "＋可能追問" : "";
          return h("li", null, h("span", { class: "n" }, i + 1), h("span", null, q.text), h("span", { class: "c" }, `${q.category}${ft ? `・${ft}` : ""}`));
        }), short > 0 ? h("li", { class: "auto" }, h("span", { class: "n" }, list.length + 1), h("span", null, `還差 ${short} 題，開始時 Ava 會自動補上`)) : null));

    const go = h("button", { class: "btn pri xl", type: "button", disabled: !has || preparing || st.busy, onclick: () => start(t, resumeSel) },
      icon("mic"), st.busy ? "準備中⋯" : "開始面試");

    replace(root,
      act ? h("div", { class: "banner" }, h("span", { class: "live-dot" }),
        h("span", { class: "t" }, h("b", null, act.status === "paused" ? "你有一場暫停中的面試" : "你有一場進行中的面試"),
          h("span", { class: "faint", style: "display:block" }, `${act.company_name}・已回答 ${act.answered} 題，停在第 ${act.current_main_no} 題`)),
        h("button", { class: "btn out sm", type: "button", onclick: () => endActive(act) }, "結束並產生報告"),
        h("a", { class: "btn pri sm", href: `#chat/${act.id}` }, "繼續面試")) : null,
      h("h1", { class: "hello" }, has ? "準備好就開始" : "先告訴 Ava 你想應徵哪個職缺"),
      h("p", null, has ? "Ava 會依你的履歷和職缺，一題一題用語音問你。" : "貼上職缺內容，Ava 會依職缺和你的履歷出題，一分鐘內就能開始練習。"),
      h("div", { class: "setup" }, jobRow, resumeRow, has ? [
        h("div", { class: "su-row" }, h("span", { class: "k" }, "長度"), h("div", { class: "v" },
          seg(Object.entries(MODES).map(([k, m]) => [k, `${m.name} ${m.n}`]), st.mode, (v) => { st.mode = v; draw(); }),
          h("span", { class: "hint" }, `約 ${mins} 分鐘（含追問）`))),
        h("div", { class: "su-row" }, h("span", { class: "k" }, "風格"), h("div", { class: "v" },
          seg(Object.entries(PERSONAS).map(([k, x]) => [k, x.name.slice(0, 2)]), st.persona, (v) => { st.persona = v; draw(); }),
          h("span", { class: "hint" }, PERSONAS[st.persona].sub))),
        h("div", { class: "su-row" }, h("span", { class: "k" }, "語言"), h("div", { class: "v" },
          seg(Object.entries(LANGS).map(([k, v]) => [k, v === "English" ? "英文" : v]), st.lang, (v) => { st.lang = v; draw(); }))),
        preview] : null),
      h("div", { class: "start" }, go,
        h("div", { class: "faint" }, has ? [h("button", { class: "linkb", type: "button", onclick: () => openJd() }, icon("plus"), "貼上新職缺"), h("span", null, "・")] : null,
          h("span", null, "開始前會請你允許使用麥克風，每題回答都會錄音存檔"))));
  }

  async function start(t, resumeSel) {
    if (S.me?.active_interview) return askReplace(S.me.active_interview, () => start(t, resumeSel));
    st.busy = true;
    draw();
    const stream = await requestMic();
    if (!stream) { st.busy = false; draw(); return; }
    stream.getTracks().forEach((x) => x.stop());     // 面試頁會重新取得；這裡只確認權限
    try {
      const body = { target_id: t.id, length_mode: st.mode, persona: st.persona, language: st.lang, recording_consent: true };
      if (S.resumeSel !== undefined) body.resume_id = resumeSel ? resumeSel.id : null;
      const r = await post("/interviews", body);
      await loadMe();
      location.hash = `#chat/${r.session.id}`;
    } catch (e) {
      st.busy = false;
      if (e.code === "ACTIVE_SESSION_EXISTS") { await loadMe(); draw(); return askReplace(S.me.active_interview, () => start(t, resumeSel)); }
      if (e.code === "SERVICE_BUSY") { toast(`目前人數較多，約 ${e.retryAfter || 30} 秒後再試`); draw(); return; }
      errorToast(e);
      draw();
    }
  }

  async function askReplace(act, then) {
    const ok = await confirmBox({ title: "你有一場暫停中的面試",
      text: `${act.company_name} 停在第 ${act.current_main_no} 題。一次只能進行一場面試，要先把它結束嗎？${act.answered ? `已回答的 ${act.answered} 題會產生報告。` : ""}`,
      ok: "結束它，開始新的", cancel: "回到那場面試" });
    if (!ok) { location.hash = `#chat/${act.id}`; return; }
    try { await post(`/interviews/${act.id}/end`, { reason: "user_ended" }); await loadMe(); renderSide(); then(); } catch (e) { errorToast(e); }
  }

  async function endActive(act) {
    const ok = await confirmBox({ title: "結束這場面試？",
      text: act.answered ? `已回答的 ${act.answered} 題會產生報告，還沒回答的題目不會評分。` : "你還沒有回答任何題目，結束後不會產生報告。",
      ok: act.answered ? "結束並產生報告" : "結束面試", cancel: "先不要" });
    if (!ok) return;
    try {
      const r = await post(`/interviews/${act.id}/end`, { reason: "user_ended" });
      await loadMe();
      renderSide();
      if (r.report) { location.hash = `#report/${act.id}`; toast("面試已結束，Ava 正在評分"); } else { toast("面試已結束，沒有回答的題目不會產生報告"); draw(); }
    } catch (e) { errorToast(e); }
  }

  async function refresh() {
    await Promise.all([loadResumes(), loadTargets(), loadMe()]);
    if (ctx.isCurrent()) draw();
  }

  draw();
  await loadQuestions();
  if (!ctx.isCurrent()) return;
  draw();
  schedulePoll();
  return () => clearTimeout(poll);
}
