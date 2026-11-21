// 職缺頁的共用頁首：職稱、公司、主要按鈕「練習這個職缺」，以及分頁（總覽／題庫／面試建議／報告）。
// 題庫（#questions/{id}）、面試建議（#prep/{id}）、報告（#targets/{id}/reports）都是同一個職缺頁的分頁。
import { h, icon } from "../dom.js";
import { pickTarget } from "../store.js";

export const JOB_TABS = (tid, n) => [
  ["overview", "總覽", `#targets/${tid}`],
  ["questions", "題庫", `#questions/${tid}`, n],
  ["prep", "面試建議", `#prep/${tid}`],
  ["reports", "報告", `#targets/${tid}/reports`],
];

/** t：GET /targets/{id} 的結果；tab：目前分頁；extra：職稱下方要多放的東西（例如原文、網址連結）。 */
export function jobHead(t, tab, { extra } = {}) {
  const j = t.job;
  const n = t.question_set?.status === "preparing" ? null : t.question_set?.item_count;
  return h("div", { class: "jhd" },
    h("a", { class: "linkb", href: "#targets", style: "margin:0 0 14px -6px" }, icon("left", 14), "我的職缺"),
    h("div", { class: "jhd-top" },
      h("div", { class: "jhd-t" }, h("h1", null, j.title),
        h("div", { class: "muted" }, [j.company_name, j.location_text].filter(Boolean).join("・")), extra || null),
      h("a", { class: "btn pri", href: "#new", onclick: () => pickTarget(t.id) }, icon("mic"), "練習這個職缺")),
    h("nav", { class: "tabs jtabs", "aria-label": "職缺內容" }, JOB_TABS(t.id, n).map(([k, label, href, cnt]) =>
      h("a", { href, class: tab === k ? "on" : "", "aria-current": tab === k ? "page" : null },
        label, cnt ? h("span", { class: "cnt" }, ` ${cnt}`) : null))));
}
