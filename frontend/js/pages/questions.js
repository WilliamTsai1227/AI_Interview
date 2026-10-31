// 題庫生成：先列目標職缺 → 單一職缺的面試題庫（生成、追問規則、預設追問、排序、編輯、刪除）。
import { del, get, patch, post, put } from "../api.js";
import { addTargetMenuItems, onAddTargetPick } from "../components/jd.js";
import { dots, h, icon, onEnter, replace, skel, trackComposition } from "../dom.js";
import { CATS, DIFF, feature, fuLabel, loadTargets, pickTarget, S } from "../store.js";
import { errorToast, logo, openMenu, setHead, toast } from "../ui.js";

export async function render(view, tid, ctx) {
  return tid ? bank(view, tid, ctx) : targetList(view, ctx);
}

async function targetList(view, ctx) {
  setHead("book", "題庫生成");
  const wrap = h("div", { class: "wrap doc" });
  view.append(wrap);
  await loadTargets().catch(() => {});
  if (!ctx.isCurrent()) return;
  const tg = S.targets || [];
  replace(wrap, h("h1", null, "題庫生成"),
    h("p", { class: "muted", style: "margin-top:6px" }, "選一個目標職缺，Ava 會依你的履歷和這個職缺幫你出題。每個目標職缺有自己的面試題庫。"),
    h("div", { style: "margin-top:20px" },
      tg.map((t) => h("a", { class: "hrow", href: `#questions/${t.id}` }, logo(t.job.company_name.slice(0, 1).toUpperCase()),
        h("span", { class: "bd" }, h("b", null, `${t.job.company_name}・${t.job.title}`),
          h("span", { class: "m" }, `${t.question_set.status === "preparing" ? "Ava 出題中⋯" : t.question_set.item_count ? `${t.question_set.item_count} 題` : "還沒有題目"}${t.match_score != null ? `・契合度 ${t.match_score}%` : ""}`)),
        h("span", { class: "ch" }, icon("right")))),
      tg.length ? null : h("p", { class: "muted", style: "padding:14px 12px" }, feature("job_search") ? "還沒有目標職缺。先從職缺庫加入，或貼上你想應徵的職缺。" : "還沒有目標職缺。先貼上你想應徵的職缺。"),
      h("button", { class: "addrow", type: "button", onclick: (e) => openMenu(e.currentTarget, addTargetMenuItems(), onAddTargetPick) }, icon("plus"), "新增目標職缺")));
}

async function bank(view, tid, ctx) {
  pickTarget(tid);
  setHead("book", "題庫生成", null, [h("button", { class: "hbtn", type: "button", onclick: () => { location.hash = "#new"; } },
    icon("mic"), h("span", { class: "hide-m" }, "用這組題目面試"))]);
  const wrap = h("div", { class: "wrap doc" }, h("div", { class: "center-load" }, dots()));
  view.append(wrap);
  const g = { cats: new Set(), diff: "medium", n: 5, gen: null };
  let target, data, editing = null, adding = false, poll = null;

  async function load() {
    [target, data] = await Promise.all([get(`/targets/${tid}`), get(`/targets/${tid}/questions`)]);
  }
  try { await load(); } catch (e) {
    if (ctx.isCurrent()) replace(wrap, h("h1", null, "找不到這個目標職缺"), h("p", null, h("a", { href: "#questions", class: "linkb" }, "回到題庫生成")));
    return;
  }
  if (!ctx.isCurrent()) return;

  const items = () => data.items;
  const note = trackComposition(h("textarea", { id: "gNote", rows: 1, placeholder: "想加強什麼？例如：多問一些帶人和 Code Review 的問題", "aria-label": "生成需求",
    onkeydown: onEnter(() => generate()) }));

  async function generate() {
    if (g.gen) return;
    try {
      const r = await post(`/targets/${tid}/questions/generate`, { note: note.value.trim(), categories: [...g.cats], difficulty: g.diff, count: g.n });
      g.gen = r.id;
      note.value = "";
      draw();
      watchGeneration();
    } catch (e) { errorToast(e); }
  }

  function watchGeneration() {
    clearTimeout(poll);
    poll = setTimeout(async () => {
      if (!ctx.isCurrent()) return;
      try {
        if (g.gen) {
          const r = await get(`/question-generations/${g.gen}`);
          if (r.status === "succeeded" || r.status === "failed") {
            g.gen = null;
            await load();
            if (r.status === "failed") toast("Ava 這次沒有出題成功，請再試一次");
            else toast(r.created_item_count ? `新增了 ${r.created_item_count} 題到 ${target.job.company_name} 面試題庫${r.skipped_duplicates ? `，有 ${r.skipped_duplicates} 題和現有題目重複，已略過` : ""}` : "面試題庫裡已經有這些題目了");
            draw();
            return;
          }
        } else if (data.status === "preparing" || data.pending_generation) {
          await load();
          draw();
          if (data.status === "ready" && !data.pending_generation) { loadTargets(); return; }
        } else return;
      } catch { /* ignore */ }
      watchGeneration();
    }, 1500);
  }

  async function act(fn, ok) {
    try { await fn(); await load(); if (ok) toast(ok); draw(); } catch (e) { errorToast(e); }
  }

  const move = (i, d) => {
    const ids = items().map((q) => q.id);
    [ids[i], ids[i + d]] = [ids[i + d], ids[i]];
    act(() => put(`/targets/${tid}/questions/order`, { item_ids: ids }));
  };

  function followupMenu(anchor, q) {
    const f = q.followup;
    openMenu(anchor, [
      { title: "這題要怎麼追問？" },
      { label: "不追問", v: "none", checked: f.mode === "none" },
      ...[1, 2, 3].map((n) => ({ label: `固定追問 ${n} 次`, sub: n === 2 ? "例如主題目加 2 個追問，共三輪" : "次數固定，內容由 Ava 依你的回答寫", v: `fixed${n}`, checked: f.mode === "fixed" && f.count === n })),
      { label: "由 Ava 判斷（最多 1 次）", sub: "回答不夠完整時才追問", v: "adaptive1", checked: f.mode === "adaptive" && f.count === 1 },
      { label: "由 Ava 判斷（最多 2 次）", v: "adaptive2", checked: f.mode === "adaptive" && f.count === 2 },
      { label: "預設追問（自己寫）", sub: "照你寫的追問依序問", v: "planned", checked: f.mode === "planned" },
    ], (v) => {
      const body = v === "none" ? { mode: "none" } : v.startsWith("fixed") ? { mode: "fixed", count: +v.slice(5) }
        : v.startsWith("adaptive") ? { mode: "adaptive", count: +v.slice(8) } : { mode: "planned" };
      act(async () => {
        const r = await put(`/targets/${tid}/questions/${q.id}/followups`, body);
        toast(r.followup.mode === "planned" && !r.followup.planned.length ? "已切換為預設追問，在下面輸入追問後按 Enter" : `已設定：${fuLabel(r.followup)}`);
      }).then(() => { if (v === "planned") wrap.querySelector(`[data-fuin="${q.id}"]`)?.focus(); });
    });
  }

  const savePlanned = (q, list) => act(() => put(`/targets/${tid}/questions/${q.id}/followups`, { mode: "planned", planned: list }));

  function row(q, i) {
    if (editing === q.id) {
      const ta = h("textarea", { rows: 3, "aria-label": "題目內容", value: q.text });
      setTimeout(() => ta.focus());
      return h("div", { class: "qrow" }, h("span", { class: "no" }, i + 1), h("div", { class: "bd" }, ta,
        h("div", { class: "row", style: "margin-top:8px" },
          h("button", { class: "btn pri sm", type: "button", onclick: () => {
            const v = ta.value.trim();
            if (!v) { toast("題目內容不能是空的"); return; }
            editing = null;
            act(() => patch(`/targets/${tid}/questions/${q.id}`, { text: v }), "已儲存");
          } }, "儲存"),
          h("button", { class: "btn ghost sm", type: "button", onclick: () => { editing = null; draw(); } }, "取消"))));
    }
    const f = q.followup;
    const planned = f.mode === "planned" ? h("div", { class: "fu-list" },
      f.planned.map((p, k) => h("div", { class: "fu-item" }, h("span", { class: "k" }, `追問 ${k + 1}`), h("span", { class: "t" }, p.text),
        h("button", { class: "ib sm", type: "button", "aria-label": "刪除這個追問",
          onclick: () => savePlanned(q, f.planned.filter((_, j) => j !== k).map((x) => x.text)) }, icon("x")))),
      f.planned.length < 5 ? trackComposition(h("input", { class: "inp", dataset: { fuin: q.id }, placeholder: "新增一個預設追問，按 Enter", "aria-label": "新增預設追問", maxlength: 200,
        onkeydown: onEnter((e) => {
          const v = e.target.value.trim();
          if (!v) return;
          savePlanned(q, [...f.planned.map((x) => x.text), v]).then(() => wrap.querySelector(`[data-fuin="${q.id}"]`)?.focus());
        }, { allowShift: false }) })) : null) : null;
    return h("div", { class: "qrow" }, h("span", { class: "no" }, i + 1),
      h("div", { class: "bd" }, h("div", { class: "qt" }, q.text),
        h("div", { class: "meta" }, `${q.category}・${DIFF[q.difficulty]}`,
          h("button", { class: "fu-pill", type: "button", onclick: (e) => followupMenu(e.currentTarget, q) }, icon("chat"), `追問：${fuLabel(f)}`, icon("chev"))),
        planned),
      h("div", { class: "ops" },
        h("button", { class: "ib sm", type: "button", "aria-label": "上移", disabled: i === 0, onclick: () => move(i, -1) }, icon("arrUp")),
        h("button", { class: "ib sm", type: "button", "aria-label": "下移", disabled: i === items().length - 1, onclick: () => move(i, 1) }, icon("arrDn")),
        h("button", { class: "ib sm", type: "button", "aria-label": "編輯", onclick: () => { editing = q.id; draw(); } }, icon("pencil")),
        h("button", { class: "ib sm", type: "button", "aria-label": "刪除", onclick: () => act(() => del(`/targets/${tid}/questions/${q.id}`), "已刪除") }, icon("trash"))));
  }

  function addRow() {
    if (!adding) return h("button", { class: "addrow", type: "button", onclick: () => { adding = true; draw(); } }, icon("plus"), "新增題目");
    const ta = h("textarea", { rows: 3, "aria-label": "題目內容", placeholder: "輸入題目，例如：你會怎麼幫助一位剛加入的初階工程師快速上手？" });
    let cat = "自訂";
    const catBtn = h("button", { class: "pill", type: "button", onclick: (e) => openMenu(e.currentTarget, [{ title: "類型" }, ...["自訂", ...CATS].map((c) => ({ label: c, v: c, checked: c === cat }))],
      (v) => { cat = v; catBtn.firstChild.textContent = v; }) }, h("span", { class: "lbl" }, cat), icon("chev"));
    setTimeout(() => { ta.focus(); ta.scrollIntoView({ block: "center" }); });
    return h("div", { class: "qrow" }, h("span", { class: "no" }, items().length + 1), h("div", { class: "bd" }, ta,
      h("div", { class: "row", style: "margin-top:8px" },
        h("button", { class: "btn pri sm", type: "button", onclick: () => {
          const v = ta.value.trim();
          if (!v) { toast("題目內容不能是空的"); return; }
          adding = false;
          act(() => post(`/targets/${tid}/questions`, { text: v, category: cat }), "已新增，Ava 會補上評分規準");
        } }, "儲存"), catBtn,
        h("button", { class: "btn ghost sm", type: "button", onclick: () => { adding = false; draw(); } }, "取消"))));
  }

  function draw() {
    if (!ctx.isCurrent()) return;
    const catLbl = g.cats.size ? [...g.cats].slice(0, 2).join("、") + (g.cats.size > 2 ? ` +${g.cats.size - 2}` : "") : "題目類型";
    const catMenu = (anchor) => openMenu(anchor, [{ title: "題目類型（可複選）" }, ...CATS.map((c) => ({ label: c, v: c, checked: g.cats.has(c) }))],
      (v) => { g.cats.has(v) ? g.cats.delete(v) : g.cats.add(v); draw(); catMenu(wrap.querySelector("#gCat")); });
    const preparing = data.status === "preparing";
    const loading = g.gen || data.pending_generation;
    replace(wrap,
      h("a", { class: "linkb", href: "#questions", style: "margin:0 0 18px -6px" }, icon("left", 14), "所有目標職缺"),
      h("div", { class: "eyebrow" }, target.job.title),
      h("h1", null, `${target.job.company_name} 面試題庫`),
      h("p", { class: "muted", style: "margin-top:6px" }, "告訴 Ava 你想加強什麼，她會依你的履歷和這個職缺出題。題目存在這個職缺的面試題庫，「新面試」選這個職缺時會照順序提問。"),
      h("div", { class: "composer", style: "margin-top:22px" }, note,
        h("div", { class: "c-bar" },
          h("button", { class: "pill", type: "button", id: "gCat", onclick: (e) => catMenu(e.currentTarget) }, icon("sliders"), h("span", { class: "lbl" }, catLbl), icon("chev")),
          h("button", { class: "pill", type: "button", onclick: (e) => openMenu(e.currentTarget, [{ title: "難度" }, ...Object.entries(DIFF).map(([k, l]) => ({ label: l, v: k, checked: g.diff === k }))], (v) => { g.diff = v; draw(); }) },
            h("span", { class: "lbl" }, DIFF[g.diff]), icon("chev")),
          h("button", { class: "pill", type: "button", onclick: (e) => openMenu(e.currentTarget, [{ title: "題數" }, ...[3, 5, 8, 10].map((n) => ({ label: `${n} 題`, v: n, checked: g.n === n }))], (v) => { g.n = v; draw(); }) },
            h("span", { class: "lbl" }, `${g.n} 題`), icon("chev")),
          h("span", { class: "sp" }),
          h("button", { class: "send", type: "button", "aria-label": "生成題目", disabled: !!loading || preparing, onclick: generate }, icon("up")))),
      h("div", { style: "margin-top:22px" },
        preparing ? [skel(2), h("p", { class: "faint", style: "padding:6px 10px" }, "Ava 正在依職缺出第一組題目⋯")] : null,
        !items().length && !loading && !preparing ? h("p", { class: "muted", style: "padding:14px 10px" }, "這個職缺還沒有題目。在上面輸入你想加強的方向，按 ↑ 讓 Ava 生成。") : null,
        items().map(row),
        loading && !preparing ? skel(2) : null,
        addRow()));
  }

  draw();
  if (data.status === "preparing" || data.pending_generation) watchGeneration();
  return () => clearTimeout(poll);
}
