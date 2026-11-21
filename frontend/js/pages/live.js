// Live 即時對談（voice_mode = live）：Ava 由 GPT-Live 即時說話，使用者直接開口回答、可以隨時打斷。
// 前端只負責：連線、顯示雙方字幕、把字幕一段一段存回後端、時間到提醒收尾。題目切分與評分在結束後由後端做。
import { api, get, post, sse } from "../api.js";
import { $, clear, dots, h, icon, replace } from "../dom.js";
import { LiveCall } from "../interview/live.js";
import { fullscreenBtn, stage } from "../interview/stage.js";
import { requestMic } from "../interview/recorder.js";
import { renderSide } from "../shell.js";
import { fmtClock, LANGS, loadMe, PERSONAS } from "../store.js";
import { closeModal, confirmBox, errorToast, openModal, setHead, toast } from "../ui.js";

const FLUSH_MS = 3000;
const OVERTIME_SEC = 60;       // 超過時間上限這麼久就自動結束
const END_DELAY_MS = 3500;     // Ava 呼叫 end_interview 後，等結語說完再斷線

export async function render(view, sid, ctx) {
  const ui = stage(view, sid);
  const chatBox = ui.chatBox;
  const dock = $("#dock");
  const st = {
    s: null, local: "loading", note: "", stream: null, call: null, talking: null, clock: null, hb: null, flusher: null,
    pending: [], events: [], bubbles: new Map(), done: new Set(), wrapped: false, ending: false, ended: false, left: false,
    reportStop: null, reportReady: false,
  };
  const alive = () => ctx.isCurrent() && !st.left;
  const live = () => st.s?.live || {};
  const remaining = () => (live().time_limit_sec || 0) - (st.s?.elapsed_sec || 0);

  // ---------- 畫面 ----------
  const scrollEnd = ui.scrollEnd;
  const sys = (text) => { chatBox.append(h("div", { class: "sysm" }, text)); scrollEnd(); };

  function bubble(turn) {
    let el = st.bubbles.get(turn.seq);
    if (!el) {
      const tx = h("div", { class: "tx" });
      el = turn.role === "interviewer" ? h("div", { class: "m-ai" }, h("div", { class: "lab" }, "Ava"), tx) : h("div", { class: "m-me" }, tx);
      el.tx = tx;
      st.bubbles.set(turn.seq, el);
      chatBox.append(el);
    }
    el.tx.textContent = turn.text;
    if (turn.role === "interviewer") { ui.setCaption(turn.text); ui.setQuestion("Ava", turn.text); }
    scrollEnd();
  }

  function clockText() {
    const r = remaining();
    return r >= 0 ? `剩 ${fmtClock(r)}` : `超時 ${fmtClock(-r)}`;
  }

  function head() {
    const s = st.s;
    const done = !s || ["completed", "aborted"].includes(s.status);
    setHead("chat", `${s?.job?.company_name || ""} Live 面試`, s && !done ? clockText() : null, [
      fullscreenBtn(),
      done ? null : h("button", { class: "hbtn", type: "button", onclick: () => pause() }, icon("pause"), h("span", { class: "hide-m" }, "暫停")),
      done ? null : h("button", { class: "hbtn line", type: "button", onclick: () => endInterview() }, "結束面試"),
    ]);
  }

  function drawDock() {
    if (!alive()) return;
    dock.hidden = false;
    const topics = live().topics || [];
    const frac = live().time_limit_sec ? Math.min(1, (st.s?.elapsed_sec || 0) / live().time_limit_sec) : 0;
    const finished = st.s?.status === "completed";
    // 有工具時依 Ava 標記的主題；沒有工具時依時間估算
    const prog = h("div", { class: "prog", title: "面試議程" }, topics.map((t, i) => h("i", {
      class: finished || st.done.has(t.id) || (!live().tools && i < Math.floor(frac * topics.length)) ? "d"
        : (!live().tools && i === Math.floor(frac * topics.length)) ? "c" : "" })));
    let left = null, mid = [], btn = null, tip = "直接開口回答就好，Ava 會自然接話；想插話也可以直接說。";
    const L = st.local;
    if (L === "loading" || L === "preparing") { mid = [dots(), L === "preparing" ? "Ava 正在讀你的履歷和職缺⋯" : "載入中⋯"]; tip = "Ava 會先看過履歷與職缺，整理好要問的重點。"; }
    else if (L === "start" || L === "rejoin") {
      mid = [L === "start" ? "準備好就開始，Ava 會先打招呼" : "對談中斷了，按下按鈕重新接上"];
      btn = h("button", { class: "btn pri sm", type: "button", onclick: () => (L === "start" ? begin() : connect()) }, icon("mic"), L === "start" ? "開始對談" : "繼續對談");
    } else if (L === "connecting") { mid = [dots(), "正在接通 Ava⋯"]; }
    else if (L === "live") {
      left = h("button", { class: "btn ghost sm", type: "button", "aria-pressed": String(!!st.call?.muted), onclick: () => toggleMute() },
        icon("mic"), st.call?.muted ? "取消靜音" : "靜音");
      mid = st.call?.muted ? ["已靜音，Ava 聽不到你"]
        : st.talking === "ava" ? [dots(), "Ava 正在說話⋯"]
          : [h("span", { class: "recdot" }), st.talking === "you" ? "正在聽你說⋯" : "Ava 在聽，直接開口回答"];
    } else if (L === "lost") {
      mid = ["連線中斷了，剛才的對話都已保存"];
      btn = h("button", { class: "btn pri sm", type: "button", onclick: () => reconnect() }, "重新連線");
    } else if (L === "paused") { mid = ["已暫停・計時停止"]; btn = h("button", { class: "btn pri sm", type: "button", onclick: () => resume() }, "繼續"); tip = "暫停期間不計時；24 小時內都能回來繼續。"; }
    else if (L === "unavailable") {
      mid = [st.note || "暫時連不上 Live 對談"];
      btn = h("button", { class: "btn pri sm", type: "button", onclick: () => connect() }, "重試");
      tip = "可以稍後再試，或結束這場面試，改用「依題目」模式。";
    } else if (L === "ending") { mid = [dots(), "正在保存對話⋯"]; tip = "保存完成後 Ava 會把對話整理成逐題評分。"; }
    else if (L === "done") {
      const rid = st.s?.status === "completed" ? st.s.id : null;
      mid = [rid ? (st.reportReady ? "面試結束，報告完成了" : "面試結束，Ava 正在整理與評分") : "面試結束"];
      btn = rid ? h("a", { class: "btn pri sm", href: `#report/${rid}` }, "查看報告") : h("a", { class: "btn pri sm", href: "#new" }, "回到新面試");
      tip = "";
    } else if (L === "error") { mid = [st.note || "連線出了點問題"]; btn = h("button", { class: "btn pri sm", type: "button", onclick: () => sync() }, "重試"); }
    replace(dock, h("div", { class: "dock-in" }, topics.length ? prog : null,
      h("div", { class: "vbar" }, left, h("div", { class: "st" }, mid), btn),
      tip ? h("div", { class: "faint", style: "text-align:center;margin-top:8px;font-size:.75rem" }, tip) : null));
  }

  const setLocal = (L, note = "") => {
    st.local = L; st.note = note;
    if (L !== "live") ui.setState(["connecting", "preparing", "ending"].includes(L) ? "thinking" : "idle");
    else if (!st.talking) ui.setState("listening");
    drawDock();
  };

  // ---------- 計時、心跳、存檔 ----------
  function startTimers() {
    clearInterval(st.clock); clearInterval(st.hb); clearInterval(st.flusher);
    st.clock = setInterval(() => {
      if (!st.s || st.s.status !== "in_progress") return;
      st.s.elapsed_sec += 1;
      const b = $("#hBadge");
      if (b) b.textContent = clockText();
      if (st.s.elapsed_sec % 15 === 0) drawDock();
      const r = remaining();
      if (!st.wrapped && st.call && r <= (live().wrap_up_sec || 90)) {
        st.wrapped = true;
        st.call.instruct(live().wrap_up_cue);
        st.events.push({ type: "live_wrap_up", at: new Date().toISOString() });
      }
      if (r <= -OVERTIME_SEC && !st.ending) finish("completed");
    }, 1000);
    st.hb = setInterval(() => {
      if (!st.s || st.s.status !== "in_progress") return;
      const events = st.events.splice(0);
      post(`/interviews/${sid}/heartbeat`, { phase: st.local, rtc_state: st.call?.pc?.connectionState || null, events })
        .catch(() => st.events.unshift(...events));
    }, (st.s?.limits?.heartbeat_sec || 15) * 1000);
    st.flusher = setInterval(() => flushTurns(), FLUSH_MS);
  }

  function stopTimers() { clearInterval(st.clock); clearInterval(st.hb); clearInterval(st.flusher); }

  /** 把累積的對話段落送回後端（失敗就留著下次再送；同一段重送不會重複）。 */
  async function flushTurns(usageSeconds = null, tries = 1) {
    if (!st.pending.length && !usageSeconds) return true;
    const turns = st.pending.splice(0, 100);
    for (let i = 0; i < tries; i++) {
      try {
        await post(`/interviews/${sid}/live/turns`, { turns, usage_seconds: usageSeconds });
        if (st.pending.length) return flushTurns(null, tries);
        return true;
      } catch (e) {
        if (e.code === "STATE_CONFLICT") return false;       // 面試已結束：不能再加
        await new Promise((r) => setTimeout(r, 600 * 2 ** i));
      }
    }
    st.pending.unshift(...turns);
    return false;
  }

  // ---------- 同步與連線 ----------
  async function sync() {
    try { st.s = await get(`/interviews/${sid}`); } catch (e) {
      if (e.status === 404) { toast("找不到這場面試"); location.hash = "#new"; return; }
      setLocal("error"); return;
    }
    if (!alive()) return;
    head();
    const s = st.s;
    if (s.status === "preparing") { setLocal("preparing"); setTimeout(() => alive() && sync(), 1500); return; }
    if (["completed", "aborted"].includes(s.status)) return finished();
    if (s.status === "in_progress" || s.status === "paused") ui.feedback.active(true);
    else ui.feedback.refresh();
    if (s.status === "ready") {
      sys(`${s.job.company_name}・${s.job.title}・Live 對談約 ${Math.round(live().time_limit_sec / 60)} 分鐘・${PERSONAS[s.persona].name}・${LANGS[s.language]}`);
      if (live().topics?.length) {
        sys(`Ava 準備聊：${live().topics.map((t) => t.title).join("、")}`);
        ui.setQuestion("Ava 準備聊的主題", live().topics.map((t) => t.title).join("、"));
      }
      return setLocal("start");
    }
    if (s.status === "paused") { sys("已回到面試・接著剛才的話題繼續"); return resume(); }
    sys("已回到面試・接著剛才的話題繼續");
    startTimers();
    return setLocal("rejoin");      // 重新整理後要使用者點一下才能播放聲音
  }

  async function ensureMic() {
    if (st.stream) return true;
    st.stream = await requestMic();
    return !!st.stream;
  }

  async function begin() {
    if (!(await ensureMic())) { setLocal("error", "需要麥克風權限才能開始"); return; }
    let r;
    try { r = await post(`/interviews/${sid}/start`, { expected_version: st.s.state_version }); } catch (e) {
      if (e.code === "STATE_CONFLICT") return sync();
      errorToast(e); return;
    }
    st.s = r.session;
    await loadMe(); renderSide();
    head(); startTimers();
    ui.feedback.active(true);
    return connect();
  }

  async function connect() {
    if (!(await ensureMic())) { setLocal("error", "需要麥克風權限才能開始"); return; }
    setLocal("connecting");
    if (st.s.avatar) await ui.startAvatar();       // 擬真人像先接上，Ava 第一句話就能對嘴；連不上就用插畫人像
    if (!alive()) return;
    const next = Math.max(live().next_seq || 0, st.call?.seq || 0);
    st.call?.teardown();
    const call = new LiveCall(st.stream, {
      onState: (x) => {
        if (call !== st.call || !alive()) return;
        if (x === "lost") { st.events.push({ type: "rtc_lost", at: new Date().toISOString() }); flushTurns(); setLocal("lost"); return; }
        if (x === "ava" || x === "you") {
          if (st.talking !== x) { st.talking = x; ui.setState(x === "ava" ? "speaking" : "listening"); if (x === "you") ui.setCaption(""); drawDock(); }
          return;
        }
        if (x === "live") setLocal("live");
      },
      onAudio: (stream) => ui.voice(stream, call.audio),
      onDelta: (t) => bubble(t),
      onTurn: (t) => { st.pending.push(t); bubble(t); },
      onTool: (name, args) => {
        st.events.push({ type: `tool_${name}`, args, at: new Date().toISOString() });
        if (name === "mark_topic_done" && args.topic_id) { st.done.add(args.topic_id); drawDock(); }
        if (name === "end_interview" && !st.ending) setTimeout(() => alive() && finish("completed"), END_DELAY_MS);
      },
      onEvent: (ev) => st.events.push({ ...ev, at: new Date().toISOString() }),
    }, next);
    st.call = call;
    try {
      await call.connect(async (sdp) => {
        const r = await post(`/interviews/${sid}/live/connect`, { sdp });
        st.s.elapsed_sec = r.elapsed_sec;
        return r.sdp;
      });
      if (st.local === "connecting") setLocal("live");
    } catch (e) {
      call.teardown();
      if (!alive()) return;
      if (e.code === "LIVE_TIME_UP") { toast(e.message); return finish("completed"); }
      if (e.code === "STATE_CONFLICT") return sync();
      if (e.code === "LIVE_UNAVAILABLE") return setLocal("unavailable", e.message);
      setLocal("unavailable", e.message || "暫時連不上 Live 對談");
    }
  }

  async function reconnect() {
    st.call?.teardown();
    await flushTurns(null, 3);
    return connect();
  }

  function toggleMute() {
    if (!st.call) return;
    st.call.mute(!st.call.muted);
    drawDock();
  }

  // ---------- 暫停、結束 ----------
  async function hangUp() {
    const call = st.call;
    st.call = null;
    const info = call ? await call.close() : null;
    await flushTurns(info?.seconds || null, 3);
  }

  async function pause() {
    if (!st.s || st.s.status !== "in_progress" || st.ending) return;
    setLocal("ending");
    await hangUp();
    ui.stopAvatar();                                // 暫停時不佔用擬真人像（依分鐘計費）
    try {
      const r = await post(`/interviews/${sid}/pause`, { expected_version: st.s.state_version, reason: "user" });
      st.s = r.session;
    } catch (e) { if (e.code !== "STATE_CONFLICT") errorToast(e); }
    setLocal("paused");
    await loadMe();
    openModal({ cls: "sm", bodyStyle: "text-align:center;padding:32px 24px 26px", body: [
      h("h2", { style: "font-size:1.2rem;font-weight:600" }, "已暫停"),
      h("p", { class: "muted", style: "margin-top:8px" }, "計時已停止，剛才的對話都保存好了。24 小時內回來，Ava 會接著剛才的話題繼續。"),
      h("div", { class: "row", style: "justify-content:center;margin-top:20px" },
        h("button", { class: "btn out", type: "button", onclick: () => { closeModal(); location.hash = "#new"; } }, "先離開"),
        h("button", { class: "btn pri", type: "button", onclick: () => { closeModal(); resume(); } }, "繼續面試")),
    ] });
  }

  async function resume() {
    try {
      const r = await post(`/interviews/${sid}/resume`, { expected_version: st.s.state_version });
      st.s = r.session;
    } catch (e) {
      if (e.code === "STATE_CONFLICT") return sync();
      errorToast(e); return;
    }
    await loadMe();
    head(); startTimers();
    return connect();
  }

  async function endInterview() {
    const talked = (live().turn_count || 0) + st.pending.length + st.bubbles.size > 0;
    const ok = await confirmBox({ title: "結束這場面試？",
      text: talked ? "Ava 會把剛才的對話整理成一題一題，逐題評分並給改善建議。" : "還沒有開始對談，結束後不會產生報告。",
      ok: talked ? "結束並產生報告" : "結束面試", cancel: "繼續面試" });
    if (ok) finish("user_ended");
  }

  async function finish(reason) {
    if (st.ending) return;
    st.ending = true;
    setLocal("ending");
    await hangUp();
    try {
      const r = await post(`/interviews/${sid}/end`, { expected_version: null, reason });
      st.s = r.session;
      st.ended = true;
    } catch (e) { st.ending = false; errorToast(e); setLocal("error"); return; }
    return finished();
  }

  async function finished() {
    stopTimers();
    ui.stopAvatar();
    ui.feedback.active(st.s.status === "completed" && !st.reportReady);    // 切題後會換成正式評分，報告好之前持續更新
    ui.feedback.refresh();
    ui.setCaption("");
    ui.setQuestion("面試結束", st.s.status === "completed" ? "每題的分數與改善建議都在下面；完整報告評完就能查看。" : "這場沒有回答內容，不會產生報告。");
    head();
    await loadMe(); renderSide();
    if (st.s.status === "completed") reportCard();
    else sys("這場沒有對談內容，所以不會產生報告。");
    setLocal("done");
  }

  function reportCard() {
    const sub = h("span", { class: "faint" }, "Ava 正在把對話整理成逐題評分，完成後會通知你");
    const tail = h("span", null, dots());
    chatBox.append(h("a", { class: "rcard", href: `#report/${sid}` }, h("span", { class: "ic" }, icon("report")),
      h("span", { style: "flex:1;min-width:0" }, h("b", null, `面試報告・${st.s.job.company_name}`), sub), tail));
    scrollEnd();
    const listen = () => {
      st.reportStop = sse(`/interviews/${sid}/report/events`, (ev, data) => {
        if (ev === "report_ready" || (ev === "snapshot" && data.status === "ready")) {
          st.reportReady = true;
          ui.feedback.active(false);
          ui.feedback.refresh();
          sub.textContent = `總分 ${data.overall_score ?? ""}・逐題評分與改善建議`;
          replace(tail, h("span", { style: "color:var(--ink-3)" }, icon("right", 18)));
          if (alive()) { toast("報告完成了"); drawDock(); }
        }
      }, () => {
        if (!st.reportReady && alive()) setTimeout(() => { if (!st.reportReady && alive()) listen(); }, 3000);
      });
    };
    listen();
  }

  // ---------- 啟動 ----------
  head();
  drawDock();
  sync();

  // 關掉分頁：補送字幕並暫停（24 小時內可回來繼續）
  const onUnload = () => {
    if (st.s?.status !== "in_progress" || st.ended) return;
    st.call?.flush();
    if (st.pending.length) api(`/interviews/${sid}/live/turns`, { method: "POST", body: { turns: st.pending.splice(0, 100) }, keepalive: true }).catch(() => {});
    api(`/interviews/${sid}/pause`, { method: "POST", body: { reason: "page_leave" }, keepalive: true }).catch(() => {});
  };
  window.addEventListener("pagehide", onUnload);

  return async () => {
    st.left = true;
    window.removeEventListener("pagehide", onUnload);
    stopTimers();
    st.reportStop?.();
    const leaving = st.s?.status === "in_progress" && !st.ended && !st.ending;
    if (leaving) await hangUp(); else st.call?.teardown();
    st.stream?.getTracks().forEach((t) => t.stop());
    ui.destroy();
    dock.hidden = true;
    clear(dock);
    if (leaving) {
      try { await post(`/interviews/${sid}/pause`, { reason: "page_leave" }); toast("面試已暫停，24 小時內都能回來繼續"); } catch { /* ignore */ }
    }
    await loadMe().catch(() => {});
  };
}
