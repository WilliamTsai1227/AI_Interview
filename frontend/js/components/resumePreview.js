// 履歷預覽：PDF 由後端逐頁轉成圖片（瀏覽器內建的 PDF 檢視器放在 iframe 裡會被擴充功能、設定擋掉，手機也不支援）；
// Word 由後端轉成段落／標題／清單／表格，用 DOM 畫成簡化版面。原始檔另外提供「新分頁開啟」與「下載」。
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

// 一頁一張圖：先放 A4 比例的空白頁佔位，同時最多下載 2 頁，避免版面跳動
function pdfPages(r, pv, urls, alive) {
  const imgs = Array.from({ length: pv.pages }, (_, i) => h("img", { class: "cv-img", alt: `第 ${i + 1} 頁`, loading: "lazy" }));
  let next = 0;
  const worker = async () => {
    while (next < imgs.length && alive()) {
      const i = next++;
      try {
        const u = URL.createObjectURL(await getBlob(`/resumes/${r.id}/pages/${i}`));
        urls.push(u);
        imgs[i].src = u;
      } catch { imgs[i].alt = `第 ${i + 1} 頁載入失敗`; }
    }
  };
  worker(); worker();
  return [h("div", { class: "cv-pages" }, imgs),
    pv.total_pages > pv.pages ? h("p", { class: "cv-note faint", style: "text-align:center" }, `只預覽前 ${pv.pages} 頁，完整內容請下載`) : null];
}

/** r：{id, filename}；onClose：關閉後要做的事（例如從設定打開時回到設定）。 */
export async function openResumePreview(r, { onClose } = {}) {
  let url = null;
  const urls = [];
  const body = h("div", { class: "cv-body" }, h("div", { class: "center-load" }, dots()));
  const actions = h("div", { class: "row", style: "flex-wrap:nowrap;gap:6px" });
  const head = h("div", { class: "mhead" },
    h("h2", { style: "flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" }, r.filename), actions,
    h("button", { class: "ib", type: "button", "aria-label": "關閉", onclick: closeModal }, icon("x")));
  const scrim = openModal({ head, body: [body], cls: "cv-modal", bodyStyle: "padding:0",
    onClose: () => { for (const u of [url, ...urls]) if (u) URL.revokeObjectURL(u); onClose?.(); } });
  const alive = () => scrim.isConnected;

  // 原始檔在背景下載，好了才顯示「新分頁開啟／下載」，不擋住預覽
  const original = getBlob(`/resumes/${r.id}/file`).then((blob) => {
    if (!alive()) return;
    url = URL.createObjectURL(blob);
    replace(actions,
      blob.type === "application/pdf" ? h("a", { class: "btn out sm", href: url, target: "_blank", rel: "noopener" }, "新分頁開啟") : null,
      h("a", { class: "btn out sm", href: url, download: r.filename }, icon("download", 14), "下載"));
  }).catch(() => {});

  try {
    const pv = await get(`/resumes/${r.id}/preview`);
    if (!alive()) return;
    replace(body,
      pv.kind === "pdf" ? pdfPages(r, pv, urls, alive)
        : pv.kind === "docx" ? [h("div", { class: "cv-note faint" }, "Word 檔以簡化版面預覽（文字、標題、清單、表格）；原始排版請下載後開啟。"), docxPage(pv.blocks)]
          : h("div", { class: "center-cta" }, icon("file"), h("p", { class: "muted" }, pv.message)));
    await original;
  } catch (e) {
    if (alive()) replace(body, h("div", { class: "center-cta" }, h("p", { class: "muted" }, "暫時無法預覽這份履歷。")));
    errorToast(e);
  }
}
