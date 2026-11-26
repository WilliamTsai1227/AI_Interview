/* Isolated, in-memory demonstration API for the UNMODIFIED frontend modules.
   Requests to /api/v1 never reach a real server. Reload resets sample edits. */
(() => {
  "use strict";
  const now = "2026-11-26T09:00:00Z";
  const clone = (value) => JSON.parse(JSON.stringify(value));
  const json = (value, status = 200) =>
    new Response(JSON.stringify(value), {
      status,
      headers: { "Content-Type": "application/json" },
    });
  const unsupported = () =>
    json(
      {
        error: {
          code: "PREVIEW_ONLY",
          message: "這是獨立風格預覽；這項 AI／檔案操作請回正式站使用。",
        },
      },
      422,
    );
  const resumes = [
    {
      id: "resume-1",
      filename: "William_Backend_Resume.pdf",
      is_primary: true,
      parse_status: "parsed",
      created_at: now,
      parsed: {
        name: "William",
        summary: "具備後端 API、資料處理與 AI 應用開發經驗。",
        years_experience: 3,
        skills: ["Python", "FastAPI", "PostgreSQL", "Docker", "RAG"],
        experiences: [{ title: "Backend Engineer", org: "產品開發團隊" }],
        projects: [],
        education: [],
      },
    },
    {
      id: "resume-2",
      filename: "William_AI_Engineer.pdf",
      is_primary: false,
      parse_status: "parsed",
      created_at: now,
      parsed: {
        name: "William",
        summary: "專注 AI 應用與檢索系統的後端工程師。",
        years_experience: 3,
        skills: ["Python", "RAG", "Redis"],
        experiences: [],
        projects: [],
        education: [],
      },
    },
  ];
  const topics = [
    ["請簡單介紹你自己，以及你最近負責的一個專案。", "自我介紹", "basic"],
    ["你會如何設計一套可擴充的 RAG 問答系統？", "系統設計", "medium"],
    ["當 API 回應時間突然增加時，你會如何定位問題？", "技術深度", "medium"],
    ["請分享一次你與團隊討論技術取捨的經驗。", "團隊合作", "medium"],
    [
      "如果資料庫同時收到重複請求，你如何確保資料一致性？",
      "情境題",
      "advanced",
    ],
    ["你為什麼想加入這個團隊？", "動機與規劃", "basic"],
    ["你如何驗證一項架構調整是否有效？", "系統設計", "medium"],
    ["你如何安排程式碼審查與技術文件？", "團隊合作", "basic"],
  ];
  let seq = 30;
  let targets = [
    ["target-1", "LINE Taiwan", "Backend Engineer", 76],
    ["target-2", "Appier", "AI Engineer", 72],
    ["target-3", "Cathay Financial", "後端工程師", 68],
  ].map(([id, company, title, score]) => ({
    id,
    created_at: now,
    match_score: score,
    job: {
      id: "job-" + id,
      company_name: company,
      title,
      source: "user",
      parse_status: "parsed",
      source_url: "",
      location_text: "台北市 · 混合辦公",
      salary_text: "待遇面議",
      tags: ["Python", "PostgreSQL", "系統設計"],
      summary:
        "參與後端服務與資料平台開發，和產品團隊一起將需求落實為穩定、容易維護的系統。",
      raw_text: `${company} ${title}\n工作內容：後端 API 設計、資料庫效能調整與服務維護。\n條件要求：熟悉 Python、SQL、容器部署與團隊協作。`,
      duties: [
        "設計與維護後端 API 及資料處理服務",
        "分析服務效能，提出可驗證的改善方案",
        "參與技術討論與 Code Review",
      ],
      requirements: [
        "熟悉 Python、SQL 與資料庫設計",
        "理解服務監控、測試與容器部署",
        "能清楚說明技術選擇與取捨",
      ],
      insight: {
        status: "ready",
        data: {
          essence: "這個團隊需要能把服務做穩、把取捨說清楚的工程師。",
          persona: ["有獨立解決問題的經驗，願意在團隊中清楚溝通"],
          focus: [
            {
              title: "能維護真實的後端服務",
              quote: "後端 API 設計與效能調整",
              meaning: "不只完成 API，還要理解資料、效能與維運之間的關係。",
              evidence: ["你如何找到效能瓶頸", "上線後如何觀察服務健康狀態"],
              story: "挑一個你曾定位問題並驗證修正的專案。",
            },
          ],
          experiences: [
            {
              kind: "must",
              text: "後端專案經驗",
              proof: "用一個實際的設計決策與結果說明。",
            },
          ],
          signals: ["面試時可能會從專案細節追問你的判斷。"],
        },
      },
    },
    resume: { id: resumes[0].id, filename: resumes[0].filename },
    question_set: { id: "set-" + id, status: "ready", item_count: 8 },
    prep: {
      status: "ready",
      checklist_total: 3,
      checklist_done: 1,
      likely_count: 3,
    },
    last_report: {
      session_id: "report-1",
      overall_score: score,
      ended_at: now,
    },
  }));
  const banks = Object.fromEntries(
    targets.map((t) => [
      t.id,
      topics.map(([text, category, difficulty], i) => ({
        id: `${t.id}-q${i}`,
        text,
        category,
        difficulty,
        order_no: i + 1,
        followup: { mode: "adaptive", count: 1, planned: [] },
      })),
    ]),
  );
  const preps = Object.fromEntries(
    targets.map((t) => [
      t.id,
      {
        id: "prep-" + t.id,
        status: "ready",
        basis: {
          job: t.job,
          resume: resumes[0],
          analyzed_at: now,
          stale: false,
        },
        match_score: t.match_score,
        verdict_title: "基礎扎實，再補強系統設計",
        verdict_text:
          "示範分析：你的後端開發經驗符合主要需求，可以再準備一個架構取捨與服務監控的具體案例。",
        strengths: ["有 API 與資料庫實作經驗", "能從專案說明技術選擇"],
        gaps: ["補充快取失效與資料一致性的設計經驗"],
        directions: [
          { label: "專案與架構取捨", probability: 85 },
          { label: "效能定位與排查", probability: 70 },
        ],
        likely_questions: topics
          .slice(0, 3)
          .map(([question], i) => ({
            id: "likely-" + i,
            question,
            priority: i ? "medium" : "high",
            why: "對應這個職缺的主要工作內容。",
            tip: "先釐清情境，再說明你的選擇、替代方案與驗證方法。",
            in_set: true,
          })),
        checklist: [
          "整理一個完整的後端專案故事",
          "畫出服務的資料流與架構",
          "準備一個效能排查案例",
        ].map((text, i) => ({ id: "check-" + i, text, is_checked: i === 0 })),
      },
    ]),
  );
  const reports = targets.map((t, i) => ({
    id: "report-" + (i + 1),
    job_label: `${t.job.company_name}・${t.job.title}`,
    target_id: t.id,
    ended_at: new Date(Date.parse(now) - i * 86400000).toISOString(),
    answered_count: 5,
    persona: "warm",
    duration_sec: 720 + i * 60,
    report_status: "ready",
    overall_score: t.match_score,
    delta_vs_prev: i === 0 ? 7 : null,
  }));
  const profile = {
    resume_id: "resume-1",
    self_intro: "有三年後端開發經驗，希望往 AI 應用與系統設計發展。",
    experience_note: "",
    portfolio_links: [],
    roles: ["Backend Engineer", "AI Engineer"],
    industries: [],
    salary_min: null,
    salary_period: "month",
    duties_pref: "後端與 AI 應用開發",
    work_hours: "",
    locations: ["台北市"],
    work_modes: ["hybrid"],
  };
  const plan = {
    id: "plan-1",
    status: "ready",
    created_at: now,
    has_resume: true,
    readiness_score: 72,
    summary: "示範分析：後端實作已具備基礎；接下來優先整理系統設計與驗證經驗。",
    profile,
    compare: {
      resume: resumes[0].filename,
      jobs: targets.map((t) => ({ company_name: t.job.company_name })),
    },
    portfolio: [],
    jobs: targets.map((t) => ({
      id: t.job.id,
      target_id: t.id,
      company_name: t.job.company_name,
      title: t.job.title,
      match_score: t.match_score,
      city: "台北市",
      salary_text: "待遇面議",
      reason: "具備符合職缺的 API 與資料庫實作經驗。",
      gaps: ["系統設計"],
    })),
    strengths: ["Python 與後端 API 的實作經驗", "能從真實專案說明資料流程"],
    gaps: [
      {
        skill: "系統設計",
        severity: "high",
        detail: "補上快取、資料一致性與監控的設計取捨。",
      },
      {
        skill: "面試表達",
        severity: "medium",
        detail: "練習用具體情境、行動與驗證結果組織回答。",
      },
    ],
    areas: [
      {
        id: "area-1",
        title: "整理系統設計案例",
        priority: "high",
        why: "把實作經驗轉成清楚的架構判斷。",
        tasks: [
          {
            id: "task-1",
            text: "畫出最近一個專案的架構與資料流",
            is_checked: true,
          },
          { id: "task-2", text: "列出兩個替代方案與取捨", is_checked: false },
          {
            id: "task-3",
            text: "準備一個效能問題的驗證結果",
            is_checked: false,
          },
        ],
      },
    ],
    progress: { done: 1, total: 3 },
  };
  const me = {
    user: {
      id: "preview-user",
      display_name: "William",
      email: "preview@example.test",
      plan: "pro",
    },
    membership: { plan_name: "Plus" },
    preferences: {
      default_length: "standard",
      default_persona: "warm",
      default_language: "zh",
      default_voice_mode: "scripted",
    },
    features: { job_search: false },
    active_interview: null,
    admin_console: false,
  };
  try {
    if (!localStorage.getItem("ai-theme"))
      localStorage.setItem("ai-theme", "light");
    localStorage.setItem(
      "ai-tours:preview-user",
      JSON.stringify(["welcome", "interview", "revise", "career"]),
    );
  } catch {
    /* Preview also works when browser storage is unavailable. */
  }

  function dispatch(path, method, body, params) {
    if (path === "/auth/refresh")
      return json({ access_token: "isolated-preview-token" });
    if (path === "/auth/logout") return unsupported();
    if (path === "/me") {
      if (method === "PATCH") {
        if (body.preferences) Object.assign(me.preferences, body.preferences);
        if (body.display_name) me.user.display_name = body.display_name;
      }
      return json(me);
    }
    if (path === "/targets" && method === "GET")
      return json({ items: targets });
    if (path === "/resumes") return json({ items: resumes, max: 5 });
    if (path.startsWith("/resumes/"))
      return json(
        resumes.find((r) => r.id === path.split("/")[2]) || resumes[0],
      );
    if (path === "/resume-revisions")
      return method === "GET" ? json({ items: [] }) : unsupported();
    if (path === "/mcp-connectors") return json({ items: [] });
    if (path === "/career/profile") {
      if (method === "PUT") Object.assign(profile, body);
      return json(profile);
    }
    if (path === "/career/plans/latest") return json({ plan });
    if (path === "/career/plans")
      return method === "GET"
        ? json({
            items: [
              {
                ...plan,
                companies: targets.map((t) => t.job.company_name),
                gap_count: 2,
              },
            ],
          })
        : unsupported();
    if (path.startsWith("/career/plans/")) {
      if (method === "PATCH") {
        const task = plan.areas
          .flatMap((a) => a.tasks)
          .find((t) => t.id === path.split("/").at(-1));
        if (task) Object.assign(task, body);
        plan.progress.done = plan.areas
          .flatMap((a) => a.tasks)
          .filter((t) => t.is_checked).length;
      }
      return json(plan);
    }
    if (path === "/interviews" && method === "GET") {
      const q = (params.get("q") || "").toLowerCase();
      return json({
        items: reports.filter((r) => r.job_label.toLowerCase().includes(q)),
        summary: { total: reports.length, avg_score: 72, best_score: 76 },
      });
    }
    const reportMatch = path.match(/^\/interviews\/([^/]+)\/report$/);
    if (reportMatch) {
      const r = reports.find((r) => r.id === reportMatch[1]) || reports[0];
      return json({
        status: "ready",
        session: { ...r, persona: "warm" },
        overall_score: r.overall_score,
        delta_vs_prev: r.delta_vs_prev,
        summary:
          "示範報告：技術概念清楚，也能說明實作經驗；回答架構問題時，可以再補充替代方案與驗證方式。",
        metrics: { filler_count: 6, avg_answer_sec: 120, speech_rate: 182 },
        focus_areas: ["用一個具體專案說明技術取捨", "補充你如何衡量與驗證成果"],
        dimensions: [
          "內容完整度",
          "技術正確性",
          "結構與邏輯",
          "表達清晰度",
          "實務深度",
        ].map((label, i) => ({
          label,
          hint: [
            "有沒有回答重點",
            "概念與方法是否正確",
            "是否有清楚的組織",
            "能否讓對方理解",
            "是否有實際案例",
          ][i],
          score: r.overall_score + i - 2,
        })),
        items: topics
          .slice(0, 5)
          .map(([question_text, category], i) => ({
            question_id: "r-q" + i,
            order_no: i + 1,
            category,
            question_text,
            status: "answered",
            followups: [],
            answer: {
              has_audio: false,
              transcript:
                "我會先釐清需求與限制，整理候選方案，再透過實作與量測來驗證。",
            },
            evaluation: {
              status: "done",
              score: 72 + i,
              strengths: ["有交代思考順序與驗證方式"],
              improvements: ["可以補充具體的限制與量測結果"],
              better_answer:
                "以你實際做過的專案為例，說明問題、方案、取捨與結果。",
            },
          })),
      });
    }
    const tm = path.match(/^\/targets\/([^/]+)(.*)$/);
    if (tm) {
      const t = targets.find((t) => t.id === tm[1]);
      if (!t) return json({ error: { message: "找不到示範職缺" } }, 404);
      const suffix = tm[2];
      if (!suffix) {
        if (method === "PATCH") {
          if (body.raw_text) t.job.raw_text = body.raw_text;
          if (body.source_url !== undefined) t.job.source_url = body.source_url;
          if (body.resume_id)
            t.resume = resumes.find((r) => r.id === body.resume_id);
        }
        if (method === "DELETE") {
          targets = targets.filter((x) => x !== t);
          return json({});
        }
        return json(t);
      }
      if (suffix === "/prep") return json(preps[t.id]);
      if (suffix === "/reports")
        return json({ items: reports.filter((r) => r.target_id === t.id) });
      if (suffix === "/questions") {
        if (method === "POST") {
          banks[t.id].push({
            id: "custom-" + ++seq,
            text: body.text,
            category: body.category,
            difficulty: "medium",
            followup: { mode: "adaptive", count: 1, planned: [] },
          });
          t.question_set.item_count = banks[t.id].length;
        }
        return json({
          id: t.question_set.id,
          status: "ready",
          items: banks[t.id],
          pending_generation: null,
        });
      }
      if (suffix === "/questions/generate" && method === "POST") {
        const count = Math.min(body.count || 3, 10);
        for (let i = 0; i < count; i++)
          banks[t.id].push({
            id: "gen-" + ++seq,
            text: `請用實際專案說明：${(body.note || "你如何判斷並驗證技術方案").slice(0, 120)}？（示範題 ${i + 1}）`,
            category: body.categories?.[0] || "系統設計",
            difficulty: body.difficulty || "medium",
            followup: { mode: "adaptive", count: 1, planned: [] },
          });
        t.question_set.item_count = banks[t.id].length;
        return json({ id: "generation-" + count });
      }
      if (suffix === "/questions/reorder") {
        const order = body.ids || body.item_ids || body.order || [];
        if (order.length)
          banks[t.id].sort((a, b) => order.indexOf(a.id) - order.indexOf(b.id));
        return json({});
      }
      const qm = suffix.match(/^\/questions\/([^/]+)(\/followups)?$/);
      if (qm) {
        const q = banks[t.id].find((q) => q.id === qm[1]);
        if (!q) return json({}, 404);
        if (qm[2]) {
          q.followup = {
            ...q.followup,
            ...body,
            planned: (body.planned || []).map((text) => ({ text })),
          };
          return json(q);
        }
        if (method === "PATCH") Object.assign(q, body);
        if (method === "DELETE") {
          banks[t.id] = banks[t.id].filter((x) => x !== q);
          t.question_set.item_count = banks[t.id].length;
        }
        return json(q);
      }
    }
    if (path.startsWith("/question-generations/"))
      return json({
        status: "succeeded",
        created_item_count: Number(path.split("-").at(-1)) || 3,
        skipped_duplicates: 0,
      });
    const cm = path.match(/^\/preps\/([^/]+)\/checklist\/([^/]+)$/);
    if (cm) {
      const prep = Object.values(preps).find((p) => p.id === cm[1]);
      const item = prep?.checklist.find((c) => c.id === cm[2]);
      if (item) Object.assign(item, body);
      return json(item || {});
    }
    return unsupported();
  }
  const nativeFetch = window.fetch.bind(window);
  window.fetch = async (input, options = {}) => {
    const url = new URL(
      typeof input === "string" ? input : input.url,
      location.href,
    );
    if (url.origin === location.origin && url.pathname.startsWith("/api/v1/")) {
      const method = (options.method || "GET").toUpperCase();
      let body = {};
      try {
        body = JSON.parse(options.body || "{}");
      } catch {
        return unsupported();
      }
      return dispatch(url.pathname.slice(7), method, body, url.searchParams);
    }
    return nativeFetch(input, options);
  };

  document.addEventListener("DOMContentLoaded", async () => {
    const { h } = await import("/js/dom.js");
    const { openModal, toast } = await import("/js/ui.js");
    const style = document.querySelector("#editorial-style");
    const controls = h(
      "div",
      {
        class: "preview-controls",
        role: "group",
        "aria-label": "比較介面風格",
      },
      h("span", { class: "preview-label" }, "風格預覽"),
    );
    let selected = "editorial";
    try {
      selected = sessionStorage.getItem("ai-preview-style") || selected;
    } catch {}
    const choices = [
      ["original", "原始介面"],
      ["editorial", "簡潔提案"],
    ];
    const buttons = choices.map(([value, label]) =>
      h(
        "button",
        {
          type: "button",
          "aria-pressed": String(selected === value),
          onclick: () => {
            selected = value;
            style.disabled = value === "original";
            buttons.forEach((b, i) =>
              b.setAttribute("aria-pressed", String(choices[i][0] === value)),
            );
            try {
              sessionStorage.setItem("ai-preview-style", value);
            } catch {}
          },
        },
        label,
      ),
    );
    controls.append(...buttons);
    style.disabled = selected === "original";
    document
      .querySelector(".head")
      .insertBefore(controls, document.querySelector("#themeBtn"));
    document
      .querySelector(".side-foot")
      .append(
        h(
          "div",
          { class: "preview-disclaimer" },
          "獨立風格預覽 · 所有內容為示範資料",
        ),
      );
    document.addEventListener(
      "click",
      (event) => {
        if (event.target.closest(".start .btn.xl")) {
          event.preventDefault();
          event.stopImmediatePropagation();
          openModal({
            title: "開始面試的位置與流程維持原樣",
            body: [
              h(
                "p",
                { class: "muted" },
                "這份原型只用來比較原始介面與新樣式，不會開啟麥克風或建立真實場次。正式站仍沿用原本的語音面試、追問與報告流程。",
              ),
              h(
                "div",
                { class: "row", style: "margin-top:20px" },
                h(
                  "a",
                  {
                    href: "#report/report-1",
                    class: "btn pri",
                    onclick: () =>
                      import("/js/ui.js").then((m) => m.closeModal()),
                  },
                  "先看報告樣式",
                ),
              ),
            ],
          });
        }
      },
      true,
    );
    if (location.hash === "#overview") location.hash = "#new";
  });
})();
