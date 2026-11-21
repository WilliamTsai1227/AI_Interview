// 面試畫面（依題目與 Live 共用）：左邊是 Ava 的即時人像與字幕，右邊是目前的問題、每題回答的評分與建議、對話紀錄。
// 側欄在進入面試時由 router 自動收起；第一次在面試畫面點擊時進入瀏覽器全螢幕（之後按 Esc 或右上角按鈕可離開）。
// 人像：後端有設定 Simli 時用擬真人像（startAvatar），連不上或中途斷線就換回瀏覽器內的插畫人像，面試照常進行。
import { get } from "../api.js";
import { dots, h, icon, replace } from "../dom.js";
import { mobile } from "../shell.js";
import { Avatar } from "./avatar.js";
import { setOutput } from "./player.js";
import { SimliAvatar } from "./simli.js";

const POLL_MS = 4000;
const STATE_TEXT = { speaking: "說話中", listening: "聆聽中", thinking: "思考中", idle: "" };

/** 瀏覽器全螢幕切換按鈕（放在頁首）。 */
export function fullscreenBtn() {
  const btn = h("button", { class: "ib hide-m", type: "button", onclick: () => toggleFullscreen() });
  const sync = () => {
    const on = !!document.fullscreenElement;
    btn.setAttribute("aria-label", on ? "離開全螢幕" : "全螢幕");
    replace(btn, icon(on ? "shrink" : "expand"));
  };
  sync();
  document.addEventListener("fullscreenchange", sync);
  return btn;
}

function toggleFullscreen() {
  if (document.fullscreenElement) document.exitFullscreen?.().catch(() => {});
  else document.documentElement.requestFullscreen?.().catch(() => {});
}

/**
 * @returns {{ chatBox, avatar, setState, setCaption, setQuestion, scrollEnd, voice, startAvatar, stopAvatar,
 *   feedback: { refresh, active }, destroy }}
 */
export function stage(view, sid) {
  const avatar = new Avatar();
  const stateTag = h("span", { class: "st-tag", hidden: true });
  const caption = h("div", { class: "cap-line", hidden: true });

  // 自己的鏡頭：只在本機預覽，不錄影也不上傳
  const selfVideo = h("video", { autoplay: true, playsinline: true });
  selfVideo.muted = true;
  const selfBox = h("div", { class: "selfview", hidden: true }, selfVideo);
  let cam = null;
  const camBtn = h("button", { class: "tile-btn", type: "button", "aria-pressed": "false", "aria-label": "開啟鏡頭（只在本機預覽）",
    onclick: () => toggleCam() }, icon("camera", 18));
  async function toggleCam() {
    if (cam) {
      cam.getTracks().forEach((t) => t.stop());
      cam = null;
      selfBox.hidden = true;
      camBtn.setAttribute("aria-pressed", "false");
      return;
    }
    try { cam = await navigator.mediaDevices.getUserMedia({ video: { width: 640, height: 480 }, audio: false }); } catch { return; }
    selfVideo.srcObject = cam;
    selfBox.hidden = false;
    camBtn.setAttribute("aria-pressed", "true");
  }

  const tile = h("div", { class: "tile" }, avatar.el,
    h("div", { class: "tile-top" }, h("span", { class: "who" }, "Ava・面試官"), stateTag),
    caption, selfBox, h("div", { class: "tile-tools" }, camBtn));

  // 右側欄
  const qLabel = h("div", { class: "faint" }, "目前的問題");
  const qText = h("div", { class: "qtext" }, "Ava 還沒開始提問");
  const fbList = h("div", { class: "fb-list" });
  const fbNote = h("p", { class: "faint fb-note", hidden: true });
  const announcer = h("div", { class: "sr-only", "aria-live": "polite" });    // 評分出來時給螢幕閱讀器念一句
  const chatBox = h("div", { class: "chat", id: "chat" });
  const fbPane = h("div", { class: "ip-pane" }, fbNote, fbList);
  const logPane = h("div", { class: "ip-pane", hidden: true }, chatBox);
  const fbCount = h("span", { class: "cnt" });
  const logDot = h("span", { class: "dot", hidden: true });
  const tabs = [
    h("button", { type: "button", class: "on", "aria-pressed": "true", onclick: () => pick(0) }, "即時評分", fbCount),
    h("button", { type: "button", "aria-pressed": "false", onclick: () => pick(1) }, "對話紀錄", logDot),
  ];
  const panes = [fbPane, logPane];
  let tab = 0;
  function pick(i) {
    tab = i;
    tabs.forEach((b, j) => { b.classList.toggle("on", i === j); b.setAttribute("aria-pressed", String(i === j)); });
    panes.forEach((p, j) => { p.hidden = i !== j; });
    if (i === 1) { logDot.hidden = true; scrollEnd(); }
  }
  const panel = h("aside", { class: "ipanel", "aria-label": "題目與評分" }, announcer,
    h("div", { class: "qcard" }, qLabel, qText),
    h("div", { class: "seg ip-tabs", role: "group" }, tabs), fbPane, logPane);

  const root = h("div", { class: "stage" }, h("div", { class: "stage-l" }, tile), panel);
  view.append(root);
  // 面試中的操作列（#dock）移到人像下面；離開時 router 會放回原位
  root.firstChild.append(document.getElementById("dock"));

  // 第一次在面試畫面點擊時進入全螢幕（瀏覽器只允許在使用者操作時切換）
  const enterOnce = () => { if (!mobile() && !document.fullscreenElement) document.documentElement.requestFullscreen?.().catch(() => {}); };
  root.addEventListener("click", enterOnce, { once: true, capture: true });

  function scrollEnd() { logPane.scrollTop = logPane.scrollHeight; }
  // 只有雙方的對話才亮紅點（開場的系統訊息不算）
  const watch = new MutationObserver((muts) => {
    const talk = muts.some((m) => m.type === "characterData" || [...m.addedNodes].some((n) => n.classList?.contains("m-ai") || n.classList?.contains("m-me")));
    if (talk && tab !== 1) logDot.hidden = false;
    scrollEnd();
  });
  watch.observe(chatBox, { childList: true, subtree: true, characterData: true });

  // ---------- 擬真人像 ----------
  let simli = null, starting = null, voiceEl = null, voiceStream = null;

  /** 換成擬真人像（已經在用就不動）。成功回傳 true；失敗保留插畫人像。 */
  function startAvatar() {
    if (simli) return Promise.resolve(true);
    if (starting) return starting;
    const sa = new SimliAvatar();
    starting = sa.start(sid).then(() => {
      if (stopped) { sa.destroy(); return false; }
      simli = sa;
      sa.onFail = () => stopAvatar();
      avatar.el.replaceWith(sa.el);
      setOutput((el) => simli?.route(el));
      if (voiceStream) voice(voiceStream, voiceEl);       // Live 已經接通：改由擬真人像播出
      return true;
    }).catch(() => { sa.destroy(); return false; }).finally(() => { starting = null; });
    return starting;
  }

  /** 換回插畫人像（暫停、結束、Simli 斷線）。原本靜音的 Live 聲音恢復播放。 */
  function stopAvatar() {
    if (!simli) return;
    const sa = simli;
    simli = null;
    setOutput(null);
    sa.el.replaceWith(avatar.el);
    sa.destroy();
    if (voiceEl) voiceEl.muted = false;
    if (voiceStream) avatar.attach(voiceStream);
  }

  /** Live：Ava 的音軌。擬真人像在用時交給它對嘴並播出，原本的 <audio> 靜音；否則給插畫人像量音量。 */
  function voice(stream, audioEl) {
    voiceStream = stream;
    voiceEl = audioEl;
    if (simli?.attach(stream)) { audioEl.muted = true; return; }
    audioEl.muted = false;
    avatar.attach(stream);
  }

  // ---------- 評分 ----------
  let active = false, timer = 0, stopped = false, seen = new Set(), first = true;
  async function refresh() {
    clearTimeout(timer);
    if (stopped) return;
    let r = null;
    try { r = await get(`/interviews/${sid}/feedback`); } catch { /* 下次再試 */ }
    if (stopped) return;
    if (r) draw(r);
    const pending = r?.items.some((i) => i.status === "pending");
    if (active || pending) timer = setTimeout(refresh, POLL_MS);
  }

  function draw({ items, provisional }) {
    fbCount.textContent = items.length ? ` ${items.length}` : "";
    fbNote.hidden = !items.length;
    fbNote.textContent = provisional ? "面試中的分數是快速參考；結束後會把對話整理成題目，重新逐題評分。" : "每題回答完就會評分，分數 0–100。";
    if (!items.length) {
      replace(fbList, h("div", { class: "fb-empty" }, h("b", null, "還沒有評分"), h("span", { class: "faint" }, "每回答完一題，這裡會出現分數、做得好的地方和改善建議。")));
      return;
    }
    replace(fbList, items.slice().reverse().map((it) => card(it, provisional)));
    const fresh = items.filter((i) => !seen.has(i.id + i.status) && i.status === "done");
    if (!first && fresh.length) {
      fbList.firstChild?.classList.add("fresh");
      announcer.textContent = fresh.map((i) => `${i.label}評分完成，${i.score} 分`).join("；");
    }
    items.forEach((i) => seen.add(i.id + i.status));
    first = false;
  }

  function card(it, provisional) {
    const score = it.status === "done" && it.score !== null
      ? h("span", { class: "fb-score num", "aria-label": `${it.score} 分` }, String(it.score))
      : it.status === "failed" ? h("span", { class: "faint" }, "暫時無法評分") : h("span", { class: "faint fb-wait" }, dots(), "評分中");
    const list = (rows, ic) => rows?.length ? h("ul", { class: "fb-ul" }, rows.map((t) => h("li", null, icon(ic, 14), h("span", null, t)))) : null;
    return h("article", { class: "fb-card" },
      h("div", { class: "fb-h" }, h("span", { class: "faint" }, it.label), score),
      h("p", { class: "fb-q" }, it.title),
      it.status === "done" ? [
        list(it.strengths, "check"),
        list(it.improvements, "right"),
        it.tip ? (provisional
          ? h("p", { class: "fb-tip" }, icon("bulb", 14), h("span", null, it.tip))
          : h("details", { class: "fb-more" }, h("summary", null, "可以這樣說"), h("p", null, it.tip))) : null,
      ] : null);
  }

  return {
    chatBox, avatar, scrollEnd, voice, startAvatar, stopAvatar,
    setState(x) {
      avatar.setState(x);
      if (x === "listening") simli?.interrupt();      // 換你說話：丟掉 Ava 還沒播完的嘴型
      stateTag.hidden = !STATE_TEXT[x];
      stateTag.textContent = STATE_TEXT[x] || "";
      stateTag.dataset.state = x;
    },
    setCaption(text) { caption.hidden = !text; caption.textContent = text ? text.slice(-140) : ""; },
    setQuestion(label, text) { qLabel.textContent = label || "目前的問題"; qText.textContent = text; },
    feedback: {
      refresh: () => refresh(),
      active(on) { const was = active; active = on; if (on && !was) refresh(); },
    },
    destroy() {
      stopped = true;
      clearTimeout(timer);
      stopAvatar();
      watch.disconnect();
      avatar.destroy();
      cam?.getTracks().forEach((t) => t.stop());
      if (document.fullscreenElement) document.exitFullscreen?.().catch(() => {});
    },
  };
}
