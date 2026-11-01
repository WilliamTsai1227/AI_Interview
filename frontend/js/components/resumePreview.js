// 履歷預覽：PDF 用瀏覽器內建檢視器（blob 網址放進 iframe）；Word 由後端轉成段落／標題／清單／表格，用 DOM 畫成簡化版面。
import { get, getBlob } from "../api.js";
import { dots, h, icon, replace } from "../dom.js";
import { closeModal, errorToast, openModal } from "../ui.js";

const run = (r) => {
  let el = h("span", null, r.t);
  if (r.b) el = h("b", null, el);
  if (r.i) el = h("i", null, el);
  if (r.u) el = h("u", null, el);
  return el;
};

function docxPage(blocks) {
  const page = h("div", { class: "cv-page" });
  let list = null;
  for (const b of blocks) {
    if (b.type !== "li") list = null;
    const style = b.align ? `text-align:${b.align}` : null;
    if (b.type === "h") page.append(h(`h${Math.min(b.level + 1, 5)}`, { style }, b.runs.map(run)));
    else if (b.type === "p") page.append(h("p", { style }, b.runs.map(run)));
    else if (b.type === "li") {
      if (!list) { list = h("ul"); page.append(list); }
      list.append(h("li", null, b.runs.map(run)));
    } else if (b.type === "table") {
      page.append(h("table", null, h("tbody", null, b.rows.map((row) => h("tr", null, row.map((c) => h("td", null, c)))))));
    }
  }
  if (!blocks.length) page.append(h("p", { class: "faint" }, "這份 Word 檔沒有可以顯示的文字。"));
  return page;
}

/** r：{id, filename}；onClose：關閉後要做的事（例如從設定打開時回到設定）。 */
export async function openResumePreview(r, { onClose } = {}) {
  let url = null;
  const body = h("div", { class: "cv-body" }, h("div", { class: "center-load" }, dots()));
  const actions = h("div", { class: "row", style: "flex-wrap:nowrap;gap:6px" });
  const head = h("div", { class: "mhead" },
    h("h2", { style: "flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" }, r.filename), actions,
    h("button", { class: "ib", type: "button", "aria-label": "關閉", onclick: closeModal }, icon("x")));
  const scrim = openModal({ head, body: [body], cls: "cv-modal", bodyStyle: "padding:0",
    onClose: () => { if (url) URL.revokeObjectURL(url); onClose?.(); } });

  try {
    const [pv, blob] = await Promise.all([get(`/resumes/${r.id}/preview`), getBlob(`/resumes/${r.id}/file`)]);
    if (!scrim.isConnected) return;
    url = URL.createObjectURL(blob);
    replace(actions,
      pv.kind === "pdf" ? h("a", { class: "btn out sm", href: url, target: "_blank", rel: "noopener" }, "新分頁開啟") : null,
      h("a", { class: "btn out sm", href: url, download: r.filename }, icon("download", 14), "下載"));
    replace(body,
      pv.kind === "pdf" ? h("iframe", { class: "cv-pdf", src: url, title: `${r.filename} 預覽` })
        : pv.kind === "docx" ? [h("div", { class: "cv-note faint" }, "Word 檔以簡化版面預覽（文字、標題、清單、表格）；原始排版請下載後開啟。"), docxPage(pv.blocks)]
          : h("div", { class: "center-cta" }, icon("file"), h("p", { class: "muted" }, pv.message)));
  } catch (e) {
    if (scrim.isConnected) replace(body, h("div", { class: "center-cta" }, h("p", { class: "muted" }, "暫時無法預覽這份履歷。")));
    errorToast(e);
  }
}
