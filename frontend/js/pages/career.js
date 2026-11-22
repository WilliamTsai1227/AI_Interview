// 求職準備（原「職涯規劃」）：使用者選一份履歷＋1–5 個想應徵的目標職缺（自我介紹、作品集、求職意向選填）
// → 分析能力差距 → 準備大項目與小項目。
import { get, patch, post, put, uploadFile } from "../api.js";
import { openJd } from "../components/jd.js";
import { openJob } from "../components/job.js";
import { openResumePreview } from "../components/resumePreview.js";
import { openSettings } from "../components/settings.js";
import { dots, h, icon, replace, skel } from "../dom.js";
import { fmtDate, loadResumes, loadTargets, resumesFull, S } from "../store.js";
import { closeModal, errorToast, logo, openMenu, openModal, setHead, toast } from "../ui.js";

const SEV = { high: "高", medium: "中", low: "低" };
const MODES = [["onsite", "進辦公室"], ["hybrid", "混合辦公"], ["remote", "完全遠端"]];
const PARSE = { pending: "等待解析", parsing: "Ava 解析中", parsed: "已解析", failed: "解析失敗" };
const STATUS = { pending: "排隊中", running: "分析中", failed: "沒有分析成功" };
const busy = (p) => p && (p.status === "pending" || p.status === "running");
const split = (s) => s.split(/[,，、\n]/).map((x) => x.trim()).filter(Boolean);
const MAX_JOBS = 5;
const jobName = (j) => `${j.company_name || "未知公司"}｜${j.title || "未命名職缺"}`;
// 公司簡稱：去掉「股份有限公司」和後面的英文名，清單裡才放得下
const shortCo = (n) => (n || "").replace(/\s*_.*$/, "").replace(/(股份)?有限公司/g, "").trim() || n;
const TABS = [["overview", "總覽"], ["plan", "準備計畫"], ["jobs", "職缺契合度"], ["portfolio", "作品集依據"]];
let lastTab = "overview";          // 換頁回來仍停在上次看的分頁

export async function render(view, _arg, ctx) {
  setHead("brief", "能力落差分析");
  const wrap = h("div", { class: "wrap doc" }, h("div", { class: "center-load" }, dots()));
  view.append(wrap);
  let profile, plan, plans = [], poll = null, editing = false, saving = false, picked = null;
  const openAreas = new Map();     // 每份分析展開了哪些準備項目，重畫時保留

  async function load() {
    [profile, { plan }, { items: plans }] = await Promise.all([get("/career/profile"), get("/career/plans/latest"), get("/career/plans"),
      loadTargets().catch(() => {})]);
    if (!S.resumes) await loadResumes().catch(() => {});
  }
  // 要比對的職缺：預設沿用上一次分析選的（還在目標職缺裡的），沒有的話選最近的 3 個
  const targets = () => S.targets || [];
  function initPicked() {
    if (picked) return;
    const alive = new Set(targets().map((t) => t.id));
    const last = (plan?.jobs || []).map((j) => j.target_id).filter((id) => id && alive.has(id));
    picked = new Set(last.length && last.length <= MAX_JOBS ? last : targets().slice(0, 3).map((t) => t.id));
  }
  const refreshList = async () => { try { ({ items: plans } = await get("/career/plans")); } catch { /* 列表只是輔助，失敗就沿用舊的 */ } };
  const isLatest = () => !plans.length || plan?.id === plans[0].id;

  // 歷次分析：每次「重新分析」都會保留，點選就切換到那一次的結果（打勾進度各自保存）
  async function show(id) {
    try { plan = await get(`/career/plans/${id}`); draw(); scrollTo({ top: 0 }); } catch (e) { errorToast(e); }
  }
  const openHistory = (e) => openMenu(e.currentTarget, [{ title: "歷次分析" }, ...plans.map((x, i) => ({
    label: `${fmtDate(x.created_at)}${i === 0 ? "（最新）" : ""}　${x.companies.slice(0, 3).map(shortCo).join("、")}${x.companies.length > 3 ? ` 等 ${x.companies.length} 個` : ""}`,
    sub: x.status === "ready" ? `準備度 ${x.readiness_score ?? "–"}%・${x.gap_count} 個缺口` : STATUS[x.status],
    v: x.id, checked: x.id === plan.id }))], show);
  try { await load(); } catch (e) { if (ctx.isCurrent()) replace(wrap, h("h1", null, "暫時無法載入"), h("p", { class: "muted" }, e.message)); return; }
  if (!ctx.isCurrent()) return;
  editing = !plan;

  function watch() {
    clearTimeout(poll);
    poll = setTimeout(async () => {
      if (!ctx.isCurrent()) return;
      try {
        const { plan: latest } = await get("/career/plans/latest");
        if (!plan || plan.id === latest?.id) plan = latest;      // 正在看舊的分析時不打斷，只在背景等最新的完成
        if (busy(latest)) { draw(); watch(); return; }
        await refreshList();
        draw();
        if (latest?.status === "ready") toast("分析完成");
      } catch { watch(); }
    }, 2000);
  }

  // 欄位內有按鈕時不能用 <label>：點擊會被轉給第一個按鈕，造成誤觸（例如工作型態無法複選）
  const field = (label, hint, el) => h(/^(INPUT|TEXTAREA)$/.test(el.tagName) ? "label" : "div", { class: "cfield" }, h("span", { class: "k" }, label, hint ? h("small", null, hint) : null), el);

  // 調整比對條件：在對話框裡改，結果頁留在後面；focus＝打開時捲到哪一區（resume／jobs／more）
  function openConditions(focus) {
    const form = profileForm({ modal: true });
    openModal({ title: "調整比對條件", cls: "cond-modal", body: [form] });
    const target = focus && form.querySelector(`[data-sec="${focus}"]`);
    if (focus === "more") form.querySelector("details.cmore")?.setAttribute("open", "");
    if (target) setTimeout(() => target.scrollIntoView({ block: "start" }));
  }

  function profileForm({ modal = false } = {}) {
    const f = {
      intro: h("textarea", { class: "inp", rows: 4, maxlength: 3000, placeholder: "用幾句話介紹你自己：背景、擅長的事、想往哪個方向發展" }, profile.self_intro),
      exp: h("textarea", { class: "inp", rows: 3, maxlength: 3000, placeholder: "履歷以外的經歷：side project、社群、志工、自學課程⋯" }, profile.experience_note),
      salary: h("input", { class: "inp", type: "number", inputmode: "numeric", min: 0, step: 1000, placeholder: "例如 70000", "aria-label": "期望薪資（最低）", value: profile.salary_min ?? "" }),
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
    initPicked();
    const resumeBox = h("div", { class: "cresume" });
    const jobBox = h("div", { class: "cjobs" });
    const actions = h("div", { class: "row", style: "margin-top:22px" });
    const linkBox = h("div", { class: "clinks" });
    const cur = () => (S.resumes || []).find((r) => r.id === f.resumeId);
    const parsing = () => { const r = cur(); return !!r && (r.parse_status === "pending" || r.parse_status === "parsing"); };
    // 履歷必填：沒有選履歷、還在解析、解析失敗都不能開始分析，並說明原因
    // 至少選 1 個職缺：這個功能就是比對履歷和想應徵的職缺
    const blocked = () => !cur() ? "先上傳履歷才能開始分析" : parsing() ? "履歷解析完成後才能開始分析"
      : cur().parse_status === "failed" ? "這份履歷沒有解析成功，請換一份或重新上傳"
        : !picked.size ? "至少選 1 個要比對的職缺" : "";
    function drawJobs() {
      const list = targets();
      for (const id of [...picked]) if (!list.some((t) => t.id === id)) picked.delete(id);
      const full = picked.size >= MAX_JOBS;
      replace(jobBox,
        list.length ? list.map((t) => {
          const on = picked.has(t.id);
          const pending = t.job.parse_status === "pending" || t.job.parse_status === "parsing";
          return h("label", { class: `cjob-pick${on ? " on" : ""}${!on && full ? " off" : ""}` },
            h("input", { type: "checkbox", checked: on, disabled: (!on && full) || pending, onchange: (e) => {
              e.target.checked ? picked.add(t.id) : picked.delete(t.id); drawJobs(); drawActions();
            } }),
            h("span", { class: "bd" }, h("b", null, jobName(t.job)),
              h("span", { class: "faint" }, pending ? "Ava 還在整理這個職缺" : [t.job.city, t.job.salary_text].filter(Boolean).join("・"))));
        }) : h("p", { class: "faint" }, "還沒有職缺。把想應徵的職缺內容貼上來，Ava 就能比對你還差多少。"),
        h("div", { class: "row", style: "margin-top:10px" },
          h("button", { class: "btn out sm", type: "button", onclick: () => openJd({ after: async (t) => {
            await loadTargets().catch(() => {});
            if (picked.size < MAX_JOBS) picked.add(t.id);
            toast("已加入我的職缺");
            drawJobs(); drawActions();
          } }) }, icon("plus"), "貼上新職缺"),
          h("span", { class: "faint num" }, `已選 ${picked.size}/${MAX_JOBS}`)));
    }
    let ptimer = null;
    const pollResumes = () => {
      clearTimeout(ptimer);
      if (!parsing()) return;
      ptimer = setTimeout(async () => {
        if (!ctx.isCurrent() || !resumeBox.isConnected) return;
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
    const fileInput = h("input", { type: "file", accept: ".pdf,.docx", hidden: true, onchange: () => { upload(fileInput.files[0]); fileInput.value = ""; } });
    function drawResume() {
      const list = S.resumes || [];
      const r = cur();
      replace(resumeBox,
        list.length ? h("button", { class: "picker", type: "button", onclick: (e) => openMenu(e.currentTarget,
          [{ title: "用哪份履歷分析" }, ...list.map((x) => ({ label: `${x.filename}${x.is_primary ? "（主要）" : ""}`, v: x.id, checked: f.resumeId === x.id }))],
          (v) => { f.resumeId = v; drawResume(); pollResumes(); }) }, icon("file"), h("b", null, r?.filename || "選擇履歷"), icon("chev"))
          : h("span", { class: "faint" }, "還沒有上傳履歷"),
        r ? h("button", { class: "btn out sm", type: "button", onclick: () => openResumePreview(r) }, icon("eye"), "預覽") : null,
        h("button", { class: "btn out sm", type: "button", disabled: resumesFull(), title: resumesFull() ? `最多保留 ${S.resumeMax} 份履歷，請到設定刪除一份` : "",
          onclick: () => fileInput.click() }, icon("upload"), list.length ? "上傳新履歷" : "上傳履歷"),
        fileInput,
        r ? h("span", { class: "faint" }, PARSE[r.parse_status] || "") : null);
      drawActions();      // 履歷狀態變了，「開始分析」能不能按也跟著變
    }
    function drawActions() {
      replace(actions,
        h("button", { class: "btn pri", type: "button", disabled: saving || !!blocked(), title: blocked(), onclick: () => save(true) },
          icon(plan ? "redo" : "search"), plan ? "用這些條件重新分析" : "開始分析"),
        h("button", { class: "btn out", type: "button", disabled: saving, onclick: () => save(false) }, "只儲存"),
        blocked() ? h("span", { class: "faint" }, blocked()) : null,
        plan ? h("button", { class: "linkb", type: "button", onclick: () => { if (modal) closeModal(); else { editing = false; draw(); } } }, "取消") : null);
    }
    const drawLinks = () => replace(linkBox,
      f.links.map((u, i) => h("div", { class: "row", style: "flex-wrap:nowrap;margin-bottom:8px" },
        h("input", { class: "inp", type: "url", placeholder: "https://github.com/你的帳號/專案", value: u, "aria-label": `作品連結 ${i + 1}`,
          oninput: (e) => { f.links[i] = e.target.value; } }),
        h("button", { class: "ib", type: "button", "aria-label": "移除連結", onclick: () => { f.links.splice(i, 1); if (!f.links.length) f.links.push(""); drawLinks(); } }, icon("x")))),
      f.links.length < 10 ? h("button", { class: "linkb", type: "button", onclick: () => { f.links.push(""); drawLinks(); setTimeout(() => linkBox.querySelectorAll("input")[f.links.length - 1]?.focus()); } }, icon("plus", 14), "新增連結") : null);
    drawResume();
    drawJobs();
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
          await post("/career/plans", { target_ids: [...picked] });
          ({ plan } = await get("/career/plans/latest"));
          await refreshList();
          editing = false;
          if (modal) closeModal();
          draw();
          watch();
        } else if (modal) { toast("已儲存，下次分析會用這些條件"); closeModal(); draw(); }
        else { toast("已儲存"); editing = !!(!plan); draw(); }
      } catch (e) { errorToast(e); } finally { saving = false; }
    }

    // 補充資料預設收起來；摘要列出已經填了哪些，使用者知道 Ava 會一起參考
    const filled = [profile.self_intro && "自我介紹", profile.experience_note && "經歷補充",
      profile.portfolio_links.length && `作品集 ${profile.portfolio_links.length} 個連結`,
      (profile.roles.length || profile.industries.length || profile.salary_min || profile.locations.length) && "求職意向"].filter(Boolean);
    return h("div", { class: `cform${modal ? " in-modal" : ""}` },
      h("h2", { "data-sec": "resume" }, h("span", { class: "step" }, "1"), "用哪份履歷"),
      field("履歷", "必填", resumeBox),
      h("h2", { "data-sec": "jobs" }, h("span", { class: "step" }, "2"), "要比對哪些職缺"),
      h("p", { class: "faint", style: "margin:-6px 0 12px" }, `從「我的職缺」選 1–${MAX_JOBS} 個想應徵的。類型相近的放在一起比，缺口會更準。`),
      jobBox,
      h("details", { class: "cmore", "data-sec": "more" },
        h("summary", null, h("h2", null, h("span", { class: "step" }, "3"), "補充資料", h("small", null, "選填")),
          h("span", { class: "faint" }, filled.length ? `已填：${filled.join("、")}` : "自我介紹、作品集、求職意向"), icon("chev")),
        h("div", { class: "bd" },
          field("自我介紹", "", f.intro), field("經歷補充", "", f.exp),
          field("作品集", "GitHub 公開 repo 會實際讀取原始碼，一行一個連結", linkBox),
          h("div", { class: "sub-h" }, "想找什麼樣的工作"),
          field("想做的職務", "", f.roles), field("產業", "", f.ind), field("希望的工作內容", "", f.duties),
          h("div", { class: "cgrid" },
            field("期望薪資（最低）", "", h("div", { class: "row", style: "flex-wrap:nowrap" }, f.salary, periodSeg)),
            field("工時偏好", "", f.hours)),
          field("地點", "", f.loc), field("工作型態", "可複選，不選＝不限", modeRow))),
      actions);
  }

  // 這次比對的條件：每一項都是按鈕，點了直接打開那一區改，改完可以馬上用新條件重新分析
  function conditions() {
    const p = plan.profile || profile;
    const cmp = plan.compare || { resume: null, jobs: [] };
    const links = p.portfolio_links || [];
    const read = (plan.portfolio || []).filter((e) => e.status === "ok").length;
    const intent = [p.roles?.length ? p.roles.join("、") : null,
      p.salary_min ? `${p.salary_period === "year" ? "年薪" : "月薪"} ${p.salary_min.toLocaleString()}+` : null].filter(Boolean).join("，");
    const chip = (ic, label, value, sub, focus, warn) => h("button", { class: `cond${warn ? " warn" : ""}`, type: "button",
      title: `調整${label}`, onclick: () => openConditions(focus) },
      icon(ic, 16), h("span", { class: "cv" }, h("small", null, label), h("b", null, value), sub ? h("small", null, sub) : null), icon("pencil", 14));
    return h("section", { class: "conds", "aria-label": "這次比對的條件" },
      h("div", { class: "conds-row" },
        chip("file", "履歷", cmp.resume || (plan.has_resume ? "已刪除的履歷" : "還沒有履歷"), null, "resume", !cmp.resume),
        chip("target", "職缺", cmp.jobs.length ? `${cmp.jobs.length} 個職缺` : "沒有比對職缺",
          cmp.jobs.map((j) => shortCo(j.company_name)).join("、") || "加入職缺，缺口會更準", "jobs", !cmp.jobs.length),
        chip("folder", "作品集", links.length ? `${links.length} 個連結` : "沒有提供", links.length ? `Ava 讀到 ${read} 個` : "GitHub repo 會讀原始碼", "more"),
        chip("sliders", "求職意向", intent || "沒有填", null, "more")),
      h("p", { class: "faint conds-f" }, `${fmtDate(plan.created_at)} 分析`,
        links.some((u) => !(plan.portfolio || []).find((x) => x.url === u && x.status === "ok")) && plan.status === "ready"
          ? "・有作品集沒讀到，詳見「作品集依據」" : ""));
  }

  // 每個作品集連結旁標出 Ava 有沒有真的讀到
  function linkStatus(u) {
    const e = (plan.portfolio || []).find((x) => x.url === u);
    const tag = !e ? (plan.status === "ready" ? h("span", { class: "tagx" }, "這次分析沒有讀取") : null)
      : e.status === "ok" ? h("span", { class: "tagx ok" }, icon("check", 12), e.kind === "profile" ? `已讀取 ${e.profile.repos.length} 個 repo 清單` : `已讀取 ${e.files_read.length} 個檔案`)
        : h("span", { class: "tagx" }, e.status === "failed" ? "讀取失敗" : "未讀取");
    return h("div", { class: "pf-link" },
      h("a", { class: "linkb", href: u, target: "_blank", rel: "noopener noreferrer" }, u.replace(/^https?:\/\//, "").slice(0, 60)), tag,
      e && e.status !== "ok" && e.error ? h("span", { class: "faint" }, e.error) : null,
      e?.stale ? h("span", { class: "faint" }, "GitHub 暫時讀不到，沿用上次讀取的結果") : null);
  }

  // Ava 實際讀了 repo 的哪些內容、看到什麼：讓使用者確認分析有依據
  function portfolioView() {
    const repos = (plan.portfolio || []).filter((e) => e.status === "ok" && e.kind === "repo");
    if (!repos.length) return null;
    const list = (title, xs, cls = "ticks") => xs?.length ? [h("div", { class: "sub-h" }, title), h("ul", { class: cls }, xs.map((t) => h("li", null, t)))] : null;
    return [
      h("div", { class: "sec-row" }, h("h2", null, "Ava 從你的作品集讀到什麼"), h("span", { class: "faint" }, "依實際原始碼判斷，不只看 README")),
      repos.map((e, i) => {
        const a = e.analysis, m = e.repo;
        const total = Object.values(m.languages || {}).reduce((x, y) => x + y, 0) || 1;
        const langs = Object.entries(m.languages || {}).filter(([, v]) => v / total >= 0.01).sort((x, y) => y[1] - x[1]).slice(0, 5).map(([k, v]) => `${k} ${Math.round(v * 100 / total)}%`);
        return h("details", { class: "acc", open: i === 0 },
          h("summary", null, icon("folder"), h("span", { class: "t" }, m.full_name),
            h("span", { class: "tagx" }, `${e.files_read.length} 個檔案`), icon("chev")),
          h("div", { class: "bd" },
            h("p", { class: "muted" }, a.purpose),
            h("div", { class: "faint", style: "margin-top:6px" }, [langs.join("・"), m.pushed_at ? `最後更新 ${fmtDate(m.pushed_at)}` : null,
              m.stars ? `★ ${m.stars}` : null, a.maturity].filter(Boolean).join("　")),
            h("div", { class: "sub-h" }, "架構"), h("p", null, a.architecture),
            h("div", { class: "sub-h" }, "技術"), h("div", { class: "row", style: "margin-top:6px" }, a.tech_stack.map((t) => h("span", { class: "tagx" }, t))),
            h("div", { class: "sub-h" }, "證明的能力"),
            h("ul", { class: "ev" }, a.skills.map((x) => h("li", null, h("b", null, x.skill), h("div", { class: "muted" }, x.evidence)))),
            list("工程實務", a.practices), list("面試可以講的亮點", a.highlights), list("可以補強", a.weaknesses, "ticks warn"),
            h("details", { class: "files" },
              h("summary", null, `Ava 讀了哪些內容（${e.files_read.length} 個檔案${e.readme_read ? "＋README" : ""}${e.commits_read ? `＋最近 ${e.commits_read} 個 commit` : ""}）`),
              h("ul", null, e.files_read.map((f) => h("li", null, h("a", { title: f, href: `${m.url}/blob/${m.default_branch}/${f}`, target: "_blank", rel: "noopener noreferrer" }, f)))))));
      }),
    ];
  }

  // 最上面：整體準備度＋離每個職缺多遠（這頁最重要的資訊）
  function hero() {
    const jobs = plan.jobs.filter((j) => j.match_score != null);
    return h("section", { class: "gap-hero" },
      h("div", { class: "gh-score" },
        h("div", { class: "big" }, h("b", { class: "num" }, `${plan.readiness_score ?? "–"}%`), h("span", null, "整體準備度")),
        plan.summary ? h("p", { class: "muted" }, plan.summary) : null),
      jobs.length ? h("ul", { class: "gh-jobs", "aria-label": "每個職缺的契合度" }, jobs.map((j) => h("li", null,
        h("button", { type: "button", class: "gh-job", onclick: () => pickTab("jobs") },
          h("span", { class: "gj-name" }, h("b", null, shortCo(j.company_name)), h("small", null, j.title)),
          h("span", { class: "gj-bar", "aria-hidden": "true" }, h("i", { style: `width:${j.match_score}%` })),
          h("span", { class: "gj-pct num" }, `${j.match_score}%`))))) : null);
  }

  let tabBar = null, tabBody = null;
  function pickTab(t) {
    lastTab = t;
    tabBar?.querySelectorAll("button").forEach((b) => { const on = b.dataset.tab === t; b.classList.toggle("on", on); b.setAttribute("aria-selected", String(on)); });
    if (tabBody) replace(tabBody, sectionView(t));
  }

  function planView() {
    const hasRepos = (plan.portfolio || []).some((e) => e.status === "ok" && e.kind === "repo");
    const tabs = TABS.filter(([k]) => k !== "portfolio" || hasRepos);
    if (!tabs.some(([k]) => k === lastTab)) lastTab = "overview";
    const doneTxt = () => `${plan.progress.done}/${plan.progress.total}`;
    tabBar = h("div", { class: "gap-tabs", role: "tablist" }, tabs.map(([k, n]) => h("button", { type: "button", role: "tab", "data-tab": k,
      class: k === lastTab ? "on" : "", "aria-selected": String(k === lastTab), onclick: () => pickTab(k) },
      n, k === "plan" && plan.progress.total ? h("span", { class: "cnt num" }, doneTxt()) : null,
      k === "jobs" && plan.jobs.length ? h("span", { class: "cnt num" }, String(plan.jobs.length)) : null)));
    tabBody = h("div", { class: "gap-body", role: "tabpanel" }, sectionView(lastTab));
    return [hero(), tabBar, tabBody];
  }

  function sectionView(t) {
    if (t === "overview") return overview();
    if (t === "plan") return planList();
    if (t === "jobs") return jobFits();
    return portfolioView();
  }

  function overview() {
    return [
      h("div", { class: "grid2" },
        h("div", null, h("h2", null, "你的優勢"), plan.strengths.length ? h("ul", { class: "ticks" }, plan.strengths.map((t) => h("li", null, t)))
          : h("p", { class: "faint" }, plan.has_resume ? "目前沒有明顯優勢可列出。" : "上傳履歷後可以看到你的優勢。")),
        h("div", null, h("h2", null, "還缺什麼"), plan.gaps.length ? h("ul", { class: "gaplist" }, plan.gaps.map((g) =>
          h("li", null, h("b", null, g.skill), h("span", { class: `tagx sev-${g.severity}` }, `${SEV[g.severity] || "中"}優先`), h("div", { class: "muted" }, g.detail)))) : h("p", { class: "faint" }, "沒有明顯缺口。"))),
      plan.areas.length ? h("div", { class: "next-step" },
        h("div", null, h("b", null, "下一步"), h("span", { class: "muted" }, `從「${plan.areas[0].title}」開始，${plan.areas[0].tasks.length} 個小項目`)),
        h("button", { class: "btn out sm", type: "button", onclick: () => pickTab("plan") }, "看準備計畫")) : null,
    ];
  }

  function planList() {
    const totalEl = h("span", { class: "faint num" }), barEl = h("i");
    const progress = () => {
      totalEl.textContent = `完成 ${plan.progress.done}/${plan.progress.total}`;
      barEl.style.width = `${plan.progress.total ? Math.round(plan.progress.done / plan.progress.total * 100) : 0}%`;
    };
    progress();
    return [
      h("div", { class: "sec-row" }, h("h2", null, "準備計畫"), totalEl),
      h("div", { class: "bar", style: "margin:-4px 0 12px" }, barEl),
      plan.areas.map((a, i) => {
        const countEl = h("span", { class: "faint num" });
        const count = () => { countEl.textContent = `${a.tasks.filter((t) => t.is_checked).length}/${a.tasks.length}`; };
        count();
        const open = openAreas.get(plan.id)?.has(a.id) ?? i === 0;
        return h("details", { class: "acc", open, ontoggle: (e) => {
          const set = openAreas.get(plan.id) || new Set(plan.areas.filter((_, k) => k === 0).map((x) => x.id));
          e.target.open ? set.add(a.id) : set.delete(a.id);
          openAreas.set(plan.id, set);
        } },
          h("summary", null, h("span", { class: "faint num" }, i + 1), h("span", { class: "t" }, a.title),
            h("span", { class: `tagx sev-${a.priority}` }, `${SEV[a.priority]}優先`), countEl, icon("chev")),
          h("div", { class: "bd" }, a.why ? h("p", { class: "muted", style: "margin-bottom:6px" }, a.why) : null,
            a.tasks.map((t) => h("label", { class: "chk" },
              // 打勾只更新數字，不重畫整頁：重畫會收合展開的項目、畫面跳回上面
              h("input", { type: "checkbox", checked: t.is_checked, onchange: async (e) => {
                const checked = e.target.checked;
                t.is_checked = checked; plan.progress.done += checked ? 1 : -1; count(); progress(); syncTabCount();
                try { await patch(`/career/plans/${plan.id}/tasks/${t.id}`, { is_checked: checked }); }
                catch (err) {
                  t.is_checked = !checked; e.target.checked = !checked; plan.progress.done += checked ? -1 : 1; count(); progress(); syncTabCount();
                  errorToast(err);
                }
              } }), h("span", null, t.text)))));
      }),
    ];
  }

  const syncTabCount = () => { const c = tabBar?.querySelector('[data-tab="plan"] .cnt'); if (c) c.textContent = `${plan.progress.done}/${plan.progress.total}`; };

  function jobFits() {
    return [
      h("div", { class: "sec-row" }, h("h2", null, "每個職缺的契合度"),
        h("span", { class: "faint" }, plan.jobs.length ? "缺口依這些職缺分析" : "沒有比對職缺")),
      plan.jobs.length ? plan.jobs.map((j) => h("div", { class: "cjob" },
        logo((j.company_name || "?").slice(0, 1).toUpperCase()),
        h("div", { class: "bd" },
          h("div", { class: "row", style: "gap:8px" }, h("b", null, j.title),
            j.match_score != null ? h("span", { class: "tagx" }, `${j.match_score}% 契合`) : null),
          h("div", { class: "muted" }, [j.company_name, j.city, j.salary_text].filter(Boolean).join("・")),
          j.reason ? h("div", { class: "muted" }, j.reason) : null,
          j.gaps.length ? h("div", { class: "row", style: "margin-top:6px" }, j.gaps.map((g) => h("span", { class: "tagx" }, `補強：${g}`))) : null,
          h("div", { class: "row", style: "margin-top:8px" },
            j.target_id ? h("a", { class: "btn out sm", href: `#targets/${j.target_id}` }, "查看職缺")
              : h("button", { class: "btn out sm", type: "button", onclick: () => openJob(j.id) }, "查看職缺"),
            j.source_url ? h("a", { class: "linkb", href: j.source_url, target: "_blank", rel: "noopener noreferrer" }, "原始職缺頁 ↗") : null))))
        : h("div", { class: "err-box", style: "margin-top:4px" },
          h("div", { class: "t" }, h("b", null, "還沒有職缺"),
            h("div", { class: "muted" }, "這次的缺口是依你的個人檔案和履歷推估的。選幾個想應徵的職缺重新分析，Ava 會比對職缺的實際要求，缺口會更準。")),
          h("button", { class: "btn out sm", type: "button", onclick: () => openConditions("jobs") }, icon("target"), "選職缺")),
    ];
  }

  // 重新分析：沿用這次比對的條件；有職缺已經移出目標職缺（或是舊版分析）時，打開條件讓使用者重新選
  async function rerun() {
    const ids = (plan.jobs || []).map((j) => j.target_id).filter(Boolean);
    const want = plan.compare?.jobs.length || 0;
    if (!ids.length || ids.length !== want || ids.length > MAX_JOBS) {
      picked = null; openConditions("jobs"); toast("有職缺已經不在「我的職缺」裡，請重新選擇要比對的職缺"); return;
    }
    try {
      await post("/career/plans", { target_ids: ids });
      ({ plan } = await get("/career/plans/latest")); await refreshList(); draw(); watch();
    } catch (e) { errorToast(e); }
  }

  function draw() {
    if (!ctx.isCurrent()) return;
    const intro = h("p", { class: "muted", style: "margin-top:6px" },
      "選一份履歷和想應徵的職缺，Ava 會分析你還差多少、該先補什麼，並排出準備計畫。");
    if (editing || !plan) { setHead("brief", "能力落差分析"); replace(wrap, h("h1", null, "能力落差分析"), intro, profileForm()); return; }
    const n = plan.compare?.jobs.length || 0;
    const hasRepos = (plan.profile?.portfolio_links || []).some((u) => /github\.com\/[^/]+\/[^/]+/i.test(u));
    const old = isLatest() ? null : h("div", { class: "hist-bar" }, icon("clock"),
      h("span", null, `你正在看 ${fmtDate(plan.created_at)} 的分析`),
      h("button", { class: "linkb", type: "button", onclick: () => show(plans[0].id) }, "回到最新"));
    const running = busy(plan) || busy(plans[0]);
    // 主要動作放在頁首右上：用一樣的條件重新分析；要換條件就點下面的條件
    setHead("brief", "能力落差分析", null, [
      plans.length > 1 ? h("button", { class: "hbtn", type: "button", onclick: openHistory }, icon("clock"), h("span", { class: "hide-m" }, `歷次分析（${plans.length}）`)) : null,
      h("button", { class: "hbtn pri", type: "button", disabled: running, title: "用這次的條件再分析一次", onclick: rerun }, icon("redo"),
        running ? "分析中⋯" : "重新分析"),
    ]);
    replace(wrap, h("h1", null, "能力落差分析"), intro, old, conditions(),
      busy(plan) ? [h("div", { class: "center-cta" }, dots(), h("p", { class: "muted" },
        plan.stage === "plan" ? `作品集讀完了，Ava 正在比對你選的 ${n} 個職缺和履歷，大約再 1 分鐘。`
          : hasRepos ? `Ava 正在讀你的 GitHub 作品集原始碼，再比對你選的 ${n} 個職缺和履歷，通常需要 1–3 分鐘。`
            : `Ava 正在比對你選的 ${n} 個職缺和履歷，通常需要 1 分鐘左右。`)), skel(3)]
        : plan.status === "failed" ? h("div", { class: "err-box", style: "margin-top:20px" }, h("span", { class: "t" }, "Ava 這次沒有分析成功。"),
          h("button", { class: "btn out sm", type: "button", onclick: rerun }, "再試一次"))
          : planView());
  }

  draw();
  if (busy(plan)) watch();
  return () => clearTimeout(poll);
}
