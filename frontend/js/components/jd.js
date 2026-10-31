// 貼上新職缺：加入目標職缺後，Ava 自動出第一組題目並產生面試建議。
import { post } from "../api.js";
import { h } from "../dom.js";
import { rerender } from "../router.js";
import { renderSide } from "../shell.js";
import { feature, loadMe, loadTargets, pickTarget } from "../store.js";
import { closeModal, openModal, toast } from "../ui.js";

export function openJd({ after } = {}) {
  const ta = h("textarea", { class: "inp", rows: 9, style: "min-height:200px", "aria-label": "職缺內容",
    placeholder: "例如：\nSenior Frontend Engineer｜Acme Inc.\n工作內容：…\n條件要求：…" });
  const url = h("input", { class: "inp", type: "url", maxlength: 1000, "aria-label": "職缺網址",
    placeholder: "https://www.104.com.tw/job/…", style: "margin-top:6px" });
  const err = h("div", { class: "form-err", style: "margin-top:8px" });
  const go = h("button", { class: "btn pri", type: "button", onclick: async () => {
    const t = ta.value.trim();
    if (t.length < 20) { err.textContent = "內容太短，至少要有職稱和工作內容"; return; }
    const link = url.value.trim();
    if (link && !/^https?:\/\/\S+$/i.test(link)) { err.textContent = "職缺網址要以 http:// 或 https:// 開頭"; return; }
    go.disabled = true;
    go.textContent = "加入中⋯";
    try {
      const target = await post("/targets", { raw_text: t, source_url: link || null });
      pickTarget(target.id);
      await Promise.all([loadTargets(), loadMe()]);
      closeModal();
      renderSide();
      toast("已加入目標職缺，Ava 正在出題");
      if (after) after(target);
      else if (location.hash !== "#new") location.hash = "#new";
      else rerender();
    } catch (e) {
      err.textContent = e.message;
      go.disabled = false;
      go.textContent = "加入目標職缺";
    }
  } }, "加入目標職缺");
  openModal({ title: "貼上新職缺", cls: "sm", body: [
    h("p", { class: "muted", style: "margin-bottom:12px" }, "把職缺內容整段貼上就好。Ava 會整理出公司、職稱、工作內容與條件，加入你的目標職缺，並自動出第一組題目。"),
    ta,
    h("label", { class: "sub-h", style: "display:block;margin-top:14px" }, "職缺網址（選填）"), url, err,
    h("div", { class: "row", style: "justify-content:flex-end;margin-top:14px" },
      h("button", { class: "btn ghost", type: "button", onclick: closeModal }, "取消"), go),
  ] });
  ta.focus();
}

/** 「新增目標職缺」選單：貼上職缺或到職缺搜尋挑選。 */
export function addTargetMenuItems() {
  return [{ icon: "plus", label: "貼上職缺內容", v: "jd" },
    ...(feature("job_search") ? [{ icon: "search", label: "到職缺搜尋挑選", v: "jobs" }] : [])];
}
export function onAddTargetPick(v) {
  if (v === "jobs") location.hash = "#jobs";
  else openJd();
}
