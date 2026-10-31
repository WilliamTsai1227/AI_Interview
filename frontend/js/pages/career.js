// 職涯規劃：個人檔案（自我介紹、求職意向）＋履歷 → 比對使用者的目標職缺、分析能力差距 → 準備大項目與小項目。
import { get, patch, post, put, uploadFile } from "../api.js";
import { openJd } from "../components/jd.js";
import { openJob } from "../components/job.js";
import { openSettings } from "../components/settings.js";
import { dots, h, icon, replace, skel } from "../dom.js";
import { fmtDate, loadResumes, S } from "../store.js";
import { errorToast, logo, openMenu, setHead, toast } from "../ui.js";

const SEV = { high: "高", medium: "中", low: "低" };
const MODES = [["onsite", "進辦公室"], ["hybrid", "混合辦公"], ["remote", "完全遠端"]];
const PARSE = { pending: "等待解析", parsing: "Ava 解析中", parsed: "已解析", failed: "解析失敗" };
const split = (s) => s.split(/[,，、\n]/).map((x) => x.trim()).filter(Boolean);

export async function render(view, _arg, ctx) {
  setHead("brief", "職涯規劃");
  const wrap = h("div", { class: "wrap doc" }, h("div", { class: "center-load" }, dots()));
  view.append(wrap);
  let profile, plan, poll = null, editing = false, saving = false;

  async function load() {
    [profile, { plan }] = await Promise.all([get("/career/profile"), get("/career/plans/latest")]);
    if (!S.resumes) await loadResumes().catch(() => {});
  }
  try { await load(); } catch (e) { if (ctx.isCurrent()) replace(wrap, h("h1", null, "暫時無法載入"), h("p", { class: "muted" }, e.message)); return; }
  if (!ctx.isCurrent()) return;
  editing = !plan;

  function watch() {
    clearTimeout(poll);
    poll = setTimeout(async () => {
      if (!ctx.isCurrent()) return;
      try {
        ({ plan } = await get("/career/plans/latest"));
        draw();
        if (plan && (plan.status === "pending" || plan.status === "running")) watch();
        else if (plan?.status === "ready") toast("分析完成");
      } catch { watch(); }
    }, 2000);
  }

  // 欄位內有按鈕時不能用 <label>：點擊會被轉給第一個按鈕，造成誤觸（例如工作型態無法複選）
  const field = (label, hint, el) => h(/^(INPUT|TEXTAREA)$/.test(el.tagName) ? "label" : "div", { class: "cfield" }, h("span", { class: "k" }, label, hint ? h("small", null, hint) : null), el);

  function profileForm() {
    const f = {
      intro: h("textarea", { class: "inp", rows: 4, maxlength: 3000, placeholder: "用幾句話介紹你自己：背景、擅長的事、想往哪個方向發展" }, profile.self_intro),
      exp: h("textarea", { class: "inp", rows: 3, maxlength: 3000, placeholder: "履歷以外的經歷：side project、社群、志工、自學課程⋯" }, profile.experience_note),
      salary: h("input", { class: "inp", type: "number", min: 0, step: 1000, placeholder: "例如 70000", value: profile.salary_min ?? "" }),
      period: profile.salary_period,
      roles: h("input", { class: "inp", placeholder: "例如：後端工程師、資料分析師（用逗號分隔）", value: profile.roles.join("、") }),
      ind: h("input", { class: "inp", placeholder: "例如：金融科技、電商、遊戲", value: profile.industries.join("、") }),
      duties: h("textarea", { class: "inp", rows: 2, maxlength: 1000, placeholder: "例如：想做後端 API 與資料處理，少一點行政" }, profile.duties_pref),
      hours: h("input", { class: "inp", maxlength: 200, placeholder: "例如：彈性上下班、不常加班", value: profile.work_hours }),
      loc: h("input", { class: "inp", placeholder: "例如：台北市、新北市", value: profile.locations.join("、") }),
      modes: new Set(profile.work_modes),
      links: profile.portfolio_links.length ? [...profile.portfolio_links] : [""],
      resumeId: profile.resume_id || (S.resumes || []).find((r) => r.is_primary)?.id || null,
    };
    const resumeBox = h("div", { class: "cresume" });
    const actions = h("div", { class: "row", style: "margin-top:22px" });
    const linkBox = h("div", { class: "clinks" });
    const cur = () => (S.resumes || []).find((r) => r.id === f.resumeId);
    const parsing = () => { const r = cur(); return !!r && (r.parse_status === "pending" || r.parse_status === "parsing"); };
    // 履歷必填：沒有選履歷、還在解析、解析失敗都不能開始分析，並說明原因
    const blocked = () => !cur() ? "先上傳履歷才能開始分析" : parsing() ? "履歷解析完成後才能開始分析"
      : cur().parse_status === "failed" ? "這份履歷沒有解析成功，請換一份或重新上傳" : "";
    let ptimer = null;
    const pollResumes = () => {
      clearTimeout(ptimer);
      if (!parsing()) return;
      ptimer = setTimeout(async () => {
        if (!ctx.isCurrent() || !editing) return;
        await loadResumes().catch(() => {});
        drawResume();
        pollResumes();
      }, 2000);
    };
    async function upload(file) {
      if (!file) return;
      if (file.size > 10 * 1024 * 1024) { toast("檔案超過 10 MB"); return; }
      toast(`正在上傳 ${file.name}⋯`);
      try {
        const uid = await uploadFile("resume", file, { filename: file.name });
        const r = await post("/resumes", { upload_id: uid });
        await loadResumes();
        f.resumeId = r.id;
        toast(r.parse_status === "parsed" ? "這份履歷之前上傳過了" : "履歷已上傳，Ava 正在解析");
        drawResume();
        pollResumes();
      } catch (e) { errorToast(e); }
    }
    const fileInput = h("input", { type: "file", accept: ".pdf,.doc,.docx", hidden: true, onchange: () => { upload(fileInput.files[0]); fileInput.value = ""; } });
    function drawResume() {
      const list = S.resumes || [];
      const r = cur();
      replace(resumeBox,
        list.length ? h("button", { class: "picker", type: "button", onclick: (e) => openMenu(e.currentTarget,
          [{ title: "用哪份履歷分析" }, ...list.map((x) => ({ label: `${x.filename}${x.is_primary ? "（主要）" : ""}`, v: x.id, checked: f.resumeId === x.id }))],
          (v) => { f.resumeId = v; drawResume(); pollResumes(); }) }, icon("file"), h("b", null, r?.filename || "選擇履歷"), icon("chev"))
          : h("span", { class: "faint" }, "還沒有上傳履歷"),
        h("button", { class: "btn out sm", type: "button", onclick: () => fileInput.click() }, icon("upload"), list.length ? "上傳新履歷" : "上傳履歷"),
        fileInput,
        r ? h("span", { class: "faint" }, PARSE[r.parse_status] || "") : null);
      drawActions();      // 履歷狀態變了，「開始分析」能不能按也跟著變
    }
    function drawActions() {
      replace(actions,
        h("button", { class: "btn pri", type: "button", disabled: saving || !!blocked(), title: blocked(), onclick: () => save(true) }, icon("search"), "儲存並開始分析"),
        h("button", { class: "btn out", type: "button", disabled: saving, onclick: () => save(false) }, "只儲存"),
        blocked() ? h("span", { class: "faint" }, blocked()) : null,
        plan ? h("button", { class: "linkb", type: "button", onclick: () => { editing = false; draw(); } }, "取消") : null);
    }
    const drawLinks = () => replace(linkBox,
      f.links.map((u, i) => h("div", { class: "row", style: "flex-wrap:nowrap;margin-bottom:8px" },
        h("input", { class: "inp", type: "url", placeholder: "https://github.com/你的帳號/專案", value: u, "aria-label": `作品連結 ${i + 1}`,
          oninput: (e) => { f.links[i] = e.target.value; } }),
        h("button", { class: "ib", type: "button", "aria-label": "移除連結", onclick: () => { f.links.splice(i, 1); if (!f.links.length) f.links.push(""); drawLinks(); } }, icon("x")))),
      f.links.length < 10 ? h("button", { class: "linkb", type: "button", onclick: () => { f.links.push(""); drawLinks(); setTimeout(() => linkBox.querySelectorAll("input")[f.links.length - 1]?.focus()); } }, icon("plus", 14), "新增連結") : null);
    drawResume();
    drawLinks();
    pollResumes();
    const periodSeg = h("div", { class: "seg" });
    const drawPeriod = () => replace(periodSeg, [["month", "月薪"], ["year", "年薪"]].map(([k, n]) =>
      h("button", { type: "button", class: f.period === k ? "on" : "", onclick: () => { f.period = k; drawPeriod(); } }, n)));
    drawPeriod();
    const modeRow = h("div", { class: "row" });
    const drawModes = () => replace(modeRow, MODES.map(([k, n]) =>
      h("button", { type: "button", class: `pill${f.modes.has(k) ? " on" : ""}`, "aria-pressed": String(f.modes.has(k)),
        onclick: () => { f.modes.has(k) ? f.modes.delete(k) : f.modes.add(k); drawModes(); } }, n)));
    drawModes();

    async function save(thenStart) {
      if (saving) return;
      saving = true;
      try {
        profile = await put("/career/profile", {
          self_intro: f.intro.value, experience_note: f.exp.value, portfolio_links: f.links.map((x) => x.trim()).filter(Boolean),
          resume_id: f.resumeId, salary_min: f.salary.value ? Number(f.salary.value) : null,
          salary_period: f.period, industries: split(f.ind.value), roles: split(f.roles.value), duties_pref: f.duties.value,
          work_hours: f.hours.value, locations: split(f.loc.value), work_modes: [...f.modes],
        });
        if (thenStart) {
          await post("/career/plans");
          ({ plan } = await get("/career/plans/latest"));
          editing = false;
          draw();
          watch();
        } else { toast("已儲存"); editing = !!(!plan); draw(); }
      } catch (e) { errorToast(e); } finally { saving = false; }
    }

    return h("div", { class: "cform" },
      h("h2", null, "關於你"),
      field("履歷", "必填，用哪一份履歷來分析", resumeBox),
      field("自我介紹", "選填", f.intro), field("經歷補充", "選填", f.exp),
      field("作品集", "GitHub、個人網站、Behance 等，一行一個連結", linkBox),
      h("h2", null, "想找什麼樣的工作"),
      field("想做的職務", "", f.roles), field("產業", "", f.ind), field("希望的工作內容", "", f.duties),
      h("div", { class: "cgrid" },
        field("期望薪資（最低）", "", h("div", { class: "row", style: "flex-wrap:nowrap" }, f.salary, periodSeg)),
        field("工時偏好", "", f.hours)),
      field("地點", "", f.loc), field("工作型態", "可複選，不選＝不限", modeRow),
      actions);
  }

  function profileSummary() {
    const p = plan.profile || profile;
    const bits = [p.roles?.length ? p.roles.join("、") : null, p.industries?.length ? p.industries.join("、") : null,
      p.salary_min ? `${p.salary_period === "year" ? "年薪" : "月薪"} ${p.salary_min.toLocaleString()}+` : null,
      p.locations?.length ? p.locations.join("、") : null, p.work_hours || null].filter(Boolean);
    return h("div", { class: "basis" }, h("div", { class: "basis-h" }, icon("user"), "這次分析依據你的個人檔案"),
      h("div", { class: "su-row" }, h("div", { class: "v" }, bits.length ? bits.join("・") : "尚未填寫求職意向")),
      h("div", { class: "su-row" }, h("span", { class: "k" }, "作品集"), h("div", { class: "v" }, (p.portfolio_links || []).length
        ? p.portfolio_links.map((u) => h("a", { class: "linkb", href: u, target: "_blank", rel: "noopener noreferrer" }, u.replace(/^https?:\/\//, "").slice(0, 48)))
        : h("span", { class: "faint" }, "沒有提供作品集連結"))),
      h("div", { class: "su-row" }, h("span", { class: "k" }, "履歷"), h("div", { class: "v" }, plan.has_resume ? "已使用你選的履歷"
        : [h("button", { class: "btn out sm", type: "button", onclick: () => openSettings("resume", { onChanged: loadResumes }) }, icon("upload"), "上傳履歷"),
          h("span", { class: "hint" }, "有履歷才能精準比對你缺什麼")])),
      h("div", { class: "basis-f" }, h("span", { class: "faint" }, `分析時間：${fmtDate(plan.created_at)}`),
        h("div", { class: "row" },
          h("button", { class: "btn out sm", type: "button", onclick: () => { editing = true; draw(); } }, icon("pencil"), "編輯檔案"),
          h("button", { class: "btn pri sm", type: "button", disabled: plan.status === "running" || plan.status === "pending",
            onclick: rerun }, icon("redo"), "重新分析"))));
  }

  function planView() {
    const pct = plan.progress.total ? Math.round(plan.progress.done / plan.progress.total * 100) : 0;
    return [
      h("div", { class: "big" }, h("b", { class: "num" }, `${plan.readiness_score ?? "–"}%`), h("span", null, "整體準備度")),
      plan.summary ? h("p", { class: "muted", style: "margin-top:14px" }, plan.summary) : null,
      h("div", { class: "grid2" },
        h("div", null, h("h2", null, "你的優勢"), plan.strengths.length ? h("ul", { class: "ticks" }, plan.strengths.map((t) => h("li", null, t)))
          : h("p", { class: "faint" }, plan.has_resume ? "目前沒有明顯優勢可列出。" : "上傳履歷後可以看到你的優勢。")),
        h("div", null, h("h2", null, "還缺什麼"), plan.gaps.length ? h("ul", { class: "gaplist" }, plan.gaps.map((g) =>
          h("li", null, h("b", null, g.skill), h("span", { class: "tagx" }, `${SEV[g.severity] || "中"}優先`), h("div", { class: "muted" }, g.detail)))) : h("p", { class: "faint" }, "沒有明顯缺口。"))),

      h("div", { class: "sec-row" }, h("h2", null, "準備計畫"),
        h("span", { class: "faint num" }, `完成 ${plan.progress.done}/${plan.progress.total}`)),
      h("div", { class: "bar", style: "margin:-4px 0 12px" }, h("i", { style: `width:${pct}%` })),
      plan.areas.map((a, i) => {
        const done = a.tasks.filter((t) => t.is_checked).length;
        return h("details", { class: "acc", open: i === 0 },
          h("summary", null, h("span", { class: "faint num" }, i + 1), h("span", { class: "t" }, a.title),
            h("span", { class: "tagx" }, `${SEV[a.priority]}優先`), h("span", { class: "faint num" }, `${done}/${a.tasks.length}`), icon("chev")),
          h("div", { class: "bd" }, a.why ? h("p", { class: "muted", style: "margin-bottom:6px" }, a.why) : null,
            a.tasks.map((t) => h("label", { class: "chk" },
              h("input", { type: "checkbox", checked: t.is_checked, onchange: async (e) => {
                t.is_checked = e.target.checked;
                try { await patch(`/career/plans/${plan.id}/tasks/${t.id}`, { is_checked: t.is_checked }); plan.progress.done += t.is_checked ? 1 : -1; draw(); }
                catch (err) { t.is_checked = !t.is_checked; e.target.checked = t.is_checked; errorToast(err); }
              } }), h("span", null, t.text)))));
      }),

      h("div", { class: "sec-row" }, h("h2", null, "你的目標職缺"),
        h("span", { class: "faint" }, plan.jobs.length ? "缺口依這些職缺分析" : "沒有目標職缺，依個人檔案分析")),
      plan.jobs.length ? plan.jobs.map((j) => h("div", { class: "cjob" },
        logo((j.company_name || "?").slice(0, 1).toUpperCase()),
        h("div", { class: "bd" },
          h("div", { class: "row", style: "gap:8px" }, h("b", null, j.title),
            j.match_score != null ? h("span", { class: "tagx" }, `${j.match_score}% 契合`) : null),
          h("div", { class: "muted" }, [j.company_name, j.city, j.salary_text].filter(Boolean).join("・")),
          j.reason ? h("div", { class: "muted" }, j.reason) : null,
          j.gaps.length ? h("div", { class: "row", style: "margin-top:6px" }, j.gaps.map((g) => h("span", { class: "tagx" }, `補強：${g}`))) : null,
          h("div", { class: "row", style: "margin-top:8px" },
            j.target_id ? h("a", { class: "btn out sm", href: `#targets/${j.target_id}` }, "查看目標職缺")
              : h("button", { class: "btn out sm", type: "button", onclick: () => openJob(j.id) }, "查看職缺"),
            j.source_url ? h("a", { class: "linkb", href: j.source_url, target: "_blank", rel: "noopener noreferrer" }, "原始職缺頁 ↗") : null))))
        : h("div", { class: "err-box", style: "margin-top:4px" },
          h("div", { class: "t" }, h("b", null, "還沒有目標職缺"),
            h("div", { class: "muted" }, "這次的缺口是依你的個人檔案和履歷推估的。加入想應徵的職缺後再按「重新分析」，Ava 會比對職缺的實際要求，缺口會更準。")),
          h("button", { class: "btn out sm", type: "button", onclick: () => openJd({ after: () => toast("已加入目標職缺，按「重新分析」就會比對這個職缺") }) },
            icon("plus"), "貼上職缺內容")),
    ];
  }

  async function rerun() {
    try { await post("/career/plans"); ({ plan } = await get("/career/plans/latest")); draw(); watch(); } catch (e) { errorToast(e); }
  }

  function draw() {
    if (!ctx.isCurrent()) return;
    const intro = h("p", { class: "muted", style: "margin-top:6px" },
      "告訴 Ava 你是誰、想找什麼工作。Ava 會比對你的目標職缺，分析你還缺什麼，並排出完整的準備計畫。");
    if (editing || !plan) { replace(wrap, h("h1", null, "職涯規劃"), intro, profileForm()); return; }
    const busy = plan.status === "pending" || plan.status === "running";
    replace(wrap, h("h1", null, "職涯規劃"), intro, profileSummary(),
      busy ? [h("div", { class: "center-cta" }, dots(), h("p", { class: "muted" },
        "Ava 正在比對你的目標職缺和履歷，通常需要 1 分鐘左右。")), skel(3)]
        : plan.status === "failed" ? h("div", { class: "err-box", style: "margin-top:20px" }, h("span", { class: "t" }, "Ava 這次沒有分析成功。"),
          h("button", { class: "btn out sm", type: "button", onclick: rerun }, "再試一次"))
          : planView());
  }

  draw();
  if (plan && (plan.status === "pending" || plan.status === "running")) watch();
  return () => clearTimeout(poll);
}
