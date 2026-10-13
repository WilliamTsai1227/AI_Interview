// 面試進行中：前端只「鏡像」後端狀態，不自行推進（題目順序與追問次數都由後端決定）。
// 流程：播放題目 → playback-finished → 點麥克風（answers）→ 錄音 → ■（上傳 → complete）→ 依 next 播放追問／下一題／結束。
import { api, get, post, sse, uploadFile, uuid } from "../api.js";
import { $, clear, dots, h, icon, replace } from "../dom.js";
import { play, prepare, setRate, stopAll } from "../interview/player.js";
import { Recorder, requestMic } from "../interview/recorder.js";
import { renderSide } from "../shell.js";
import { fmtClock, LANGS, loadMe, MODES, PERSONAS, S } from "../store.js";
import { closeModal, confirmBox, errorToast, openModal, setHead, toast } from "../ui.js";

let devMode = null;   // 後端沒有 OpenAI 金鑰：用瀏覽器語音辨識產生字幕
async function isDev() {
  if (devMode === null) {
    try { devMode = (await fetch("/api/v1/health").then((r) => r.json())).ai === "fake"; } catch { devMode = false; }
  }
  return devMode;
}

export async function render(view, sid, ctx) {
  if (!sid) { location.hash = "#new"; return; }
  const chatBox = h("div", { class: "chat", id: "chat" });
  view.append(chatBox);
  const dock = $("#dock");
  const st = {
    s: null, local: "loading", stream: null, rec: null, attempt: null, ack: null, ackPlaying: null, clock: null,
    hb: null, events: [], failedUpload: null, answerEl: null, recTimer: null, left: false, reportStop: null,
  };
  const alive = () => ctx.isCurrent() && !st.left;
  const lang = () => st.s?.language || "zh";
  setRate(S.me?.preferences?.voice_speed ?? 1.15);

  // ---------- 畫面 ----------
  const scrollEnd = () => { const v = $("#view"); v.scrollTop = v.scrollHeight; };
  const sys = (text) => { chatBox.append(h("div", { class: "sysm" }, text)); scrollEnd(); };

  function aiMsg(label, text, speech) {
    const tx = h("div", { class: "tx" });
    const el = h("div", { class: "m-ai" }, label ? h("div", { class: "lab" }, label) : null, tx);
    chatBox.append(el);
    scrollEnd();
    return {
      el, tx,
      type: () => new Promise((resolve) => {
        let i = 0;
        tx.classList.add("cursor");
        const step = () => {
          if (!tx.isConnected) return resolve();
          i = Math.min(text.length, i + 2);
          tx.textContent = text.slice(0, i);
          scrollEnd();
          if (i < text.length) setTimeout(step, 40); else { tx.classList.remove("cursor"); resolve(); }
        };
        step();
      }),
      acts: () => el.append(h("div", { class: "acts" },
        speech ? h("button", { class: "ib sm", type: "button", "aria-label": "重播這題", onclick: () => play(speech, lang()) }, icon("speak")) : null,
        h("button", { class: "ib sm", type: "button", "aria-label": "複製題目", onclick: () => { navigator.clipboard?.writeText(text); toast("已複製題目"); } }, icon("copy")))),
    };
  }

  function questionLabel(q) {
    const main = `第 ${q.order_no} 題`;
    if (q.kind === "main") return `${main}・${q.category}`;
    const total = q.followup_mode === "adaptive" ? "" : `/${q.followup_target}`;
    return `${main}・追問 ${q.followup_no}${total}`;
  }

  function badge() {
    const s = st.s;
    if (!s) return "";
    const p = s.progress, q = s.current_question;
    const fu = q && q.kind === "followup" ? `・追問 ${q.followup_no}${q.followup_mode === "adaptive" ? "" : `/${q.followup_target}`}` : "";
    return `${Math.min(p.current_main_no, p.total_main)} / ${p.total_main}${fu}`;
  }

  function head() {
    const s = st.s;
    const done = !s || ["completed", "aborted"].includes(s.status);
    setHead("chat", `${s?.job?.company_name || ""} 模擬面試`, s ? badge() : null, [
      h("span", { class: "faint num hide-m", style: "display:inline-flex;align-items:center;gap:5px;margin-right:4px" }, icon("clock", 14),
        h("span", { id: "ivT" }, fmtClock(s?.elapsed_sec || 0))),
      done ? null : h("button", { class: "hbtn", type: "button", onclick: () => pause() }, icon("pause"), h("span", { class: "hide-m" }, "暫停")),
      done ? null : h("button", { class: "hbtn line", type: "button", onclick: () => endInterview() }, "結束面試"),
    ]);
  }

  function updateBadge() {
    const b = $("#hBadge");
    if (b) b.textContent = badge();
    drawDock();
  }

  function drawDock() {
    if (!alive()) return;
    const s = st.s;
    dock.hidden = false;
    const total = s?.progress.total_main || 0, cur = s?.progress.current_main_no || 1;
    const done = s && ["completed", "aborted"].includes(s.status);
    const prog = h("div", { class: "prog" }, Array.from({ length: total }, (_, i) =>
      h("i", { class: done || i < cur - 1 ? "d" : i === cur - 1 ? "c" : "" })));
    let left = null, mid = [], btn = null, tip = "題目會依面試題庫順序提問，每題回答都會錄音存檔。";
    const L = st.local;
    if (L === "loading" || L === "preparing") { mid = [dots(), L === "preparing" ? "Ava 準備中⋯" : "載入中⋯"]; btn = h("button", { class: "micbtn", type: "button", disabled: true }, icon("mic")); }
    else if (L === "paused") { mid = ["已暫停・計時停止"]; btn = h("button", { class: "btn pri sm", type: "button", onclick: () => resume() }, "繼續"); tip = "暫停期間不計時；24 小時內都能回來繼續。"; }
    else if (L === "speaking") { mid = [dots(), "Ava 正在說話⋯"]; btn = h("button", { class: "micbtn", type: "button", disabled: true, "aria-label": "開始回答" }, icon("mic")); }
    else if (L === "blocked") { mid = ["瀏覽器擋住了自動播放"]; btn = h("button", { class: "btn pri sm", type: "button", onclick: () => st.unblock?.() }, icon("speak"), "播放題目"); }
    else if (L === "ready") {
      left = h("button", { class: "btn ghost sm", type: "button", onclick: () => skip() }, icon("next"), "跳過");
      mid = ["輪到你了，點麥克風開始回答"];
      btn = h("button", { class: "micbtn", type: "button", id: "vMic", "aria-label": "開始回答", onclick: () => startAnswer() }, icon("mic"));
    } else if (L === "recording") {
      left = h("button", { class: "btn ghost sm", type: "button", onclick: () => rerecord() }, icon("redo"), "重錄");
      mid = [h("span", { class: "recdot" }), h("span", { class: "num", id: "aT" }, fmtClock(Math.floor((st.rec?.durationMs || 0) / 1000))),
        h("span", { class: "wave", "aria-hidden": "true" }, Array.from({ length: 48 }, (_, i) => h("i", { style: `animation-delay:${(i * 137 % 1000) / 1000}s` })))];
      btn = h("button", { class: "micbtn", type: "button", id: "vStop", "aria-label": "完成回答", onclick: () => finishAnswer("user_click") }, icon("stop"));
      tip = "正在錄音，說完按 ■ 送出；想重講就按「重錄」。";
    } else if (L === "uploading") { mid = [dots(), "正在上傳你的回答⋯"]; btn = h("button", { class: "micbtn", type: "button", disabled: true }, icon("stop")); tip = "上傳完成前不會進到下一題，錄音不會遺失。"; }
    else if (L === "upload_failed") {
      mid = ["上傳失敗，你的錄音還在"];
      btn = h("button", { class: "btn pri sm", type: "button", onclick: () => submitAnswer(st.failedUpload) }, "重新上傳");
      tip = "請確認網路後重新上傳；不會前進到下一題。";
    } else if (L === "done") {
      const rid = s?.status === "completed" ? s.id : null;
      mid = [rid ? (st.reportReady ? "面試結束，報告完成了" : "面試結束，Ava 正在評分") : "面試結束"];
      btn = rid ? h("a", { class: "btn pri sm", href: `#report/${rid}` }, "查看報告") : h("a", { class: "btn pri sm", href: "#new" }, "回到新面試");
    } else if (L === "error") { mid = ["連線出了點問題"]; btn = h("button", { class: "btn pri sm", type: "button", onclick: () => sync() }, "重試"); }
    replace(dock, h("div", { class: "dock-in" }, prog,
      h("div", { class: "vbar" }, left, h("div", { class: "st" }, mid), btn),
      h("div", { class: "faint", style: "text-align:center;margin-top:8px;font-size:.75rem" }, tip),
      devMode && L === "recording" ? h("div", { class: "devnote" }, "開發模式：沒有設定 OpenAI 金鑰，回答文字來自瀏覽器語音辨識") : null));
  }

  const setLocal = (L) => { st.local = L; drawDock(); };

  function startClock() {
    clearInterval(st.clock);
    st.clock = setInterval(() => {
      if (!st.s || st.s.status !== "in_progress") return;
      st.s.elapsed_sec += 1;
      const el = $("#ivT");
      if (el) el.textContent = fmtClock(st.s.elapsed_sec);
    }, 1000);
  }

  function startHeartbeat() {
    clearInterval(st.hb);
    st.hb = setInterval(() => {
      if (!st.s || st.s.status !== "in_progress") return;
      const events = st.events.splice(0);
      post(`/interviews/${sid}/heartbeat`, { phase: st.s.phase, events }).catch(() => st.events.unshift(...events));
    }, (st.s?.limits?.heartbeat_sec || 15) * 1000);
  }

  // ---------- 同步與錯誤 ----------
  async function call(fn) {
    try { return await fn(); } catch (e) {
      if (e.code === "STATE_CONFLICT") { await sync(); return null; }
      throw e;
    }
  }

  /** 依伺服器狀態繼續（重新整理、409、從別頁回來）。 */
  async function sync() {
    try { st.s = await get(`/interviews/${sid}`); } catch (e) {
      if (e.status === 404) { toast("找不到這場面試"); location.hash = "#new"; return; }
      setLocal("error"); return;
    }
    if (!alive()) return;
    head();
    const s = st.s;
    if (s.status === "preparing") { setLocal("preparing"); setTimeout(() => alive() && sync(), 1000); return; }
    if (s.status === "ready") return begin();
    if (["completed", "aborted"].includes(s.status)) return finished(false);
    if (s.status === "paused") {
      sys(`已回到面試・從第 ${s.progress.current_main_no} 題繼續`);
      return resume();
    }
    return continueFromPhase();
  }

  async function ensureMic() {
    if (st.stream) return true;
    st.stream = await requestMic();
    if (!st.stream) return false;
    st.stream.getAudioTracks().forEach((t) => { t.enabled = false; });
    return true;
  }

  async function begin() {
    if (!(await ensureMic())) { setLocal("error"); return; }
    const s = st.s;
    sys(`${s.job.company_name}・${s.job.title}・${MODES[s.length_mode].name} ${s.progress.total_main} 題・${PERSONAS[s.persona].name}・${LANGS[s.language]}`);
    const r = await call(() => post(`/interviews/${sid}/start`, { expected_version: s.state_version }));
    if (!r) return;
    st.s = r.session;
    await loadMe(); renderSide();
    head(); startClock(); startHeartbeat();
    await askCurrent(r.intro);
  }

  async function continueFromPhase() {
    const s = st.s;
    startClock(); startHeartbeat();
    if (!(await ensureMic())) { setLocal("error"); return; }
    const q = s.current_question;
    if (s.phase === "asking") return askCurrent();
    if (s.phase === "answering" && s.current_attempt) {
      // 錄音中斷（重新整理或離開）：丟掉這次錄音，回到「輪到你了」
      const r = await call(() => post(`/interviews/${sid}/attempts/${s.current_attempt.id}/discard`, { expected_version: s.state_version }));
      if (!r) return;
      st.s = r.session;
    }
    if (st.s.phase === "finalizing") { setLocal("uploading"); setTimeout(() => alive() && sync(), 1500); return; }
    const m = aiMsg(questionLabel(q), q.text, q);
    m.tx.textContent = q.text;
    m.acts();
    updateBadge();
    setLocal("ready");
  }

  /** 播放目前題目（主題目或追問），播完送 playback-finished。 */
  async function askCurrent(intro) {
    const q = st.s.current_question;
    updateBadge();
    setLocal("speaking");
    const text = intro ? `${intro.text}\n\n${q.text}` : q.text;
    const m = aiMsg(questionLabel(q), text, q);
    const typing = m.type();
    if (intro) await speak(intro);
    await speak(q);
    await typing;
    if (!alive() || st.local === "paused") return;
    m.acts();
    const r = await call(() => post(`/interviews/${sid}/questions/${q.id}/playback-finished`, { expected_version: st.s.state_version }));
    if (!r) return;
    st.s = r.session;
    updateBadge();
    setLocal("ready");
  }

  async function speak(speech) {
    const r = await play(speech, lang());
    if (r.blocked && alive()) {
      st.events.push({ type: "autoplay_blocked", at: new Date().toISOString() });
      await new Promise((resolve) => { st.unblock = () => { st.unblock = null; resolve(); }; setLocal("blocked"); });
      setLocal("speaking");
      await play(speech, lang());
    }
  }

  // ---------- 作答 ----------
  async function startAnswer() {
    if (st.local !== "ready") return;
    setLocal("speaking");
    const q = st.s.current_question;
    let r;
    try { r = await call(() => post(`/interviews/${sid}/questions/${q.id}/answers`, { expected_version: st.s.state_version })); }
    catch (e) { errorToast(e); setLocal("ready"); return; }
    if (!r) return;
    st.s = r.session;
    st.attempt = r.attempt;
    st.ack = r.ack;
    const cap = h("span", { class: "cap" }, "錄音中⋯");
    st.answerEl = h("div", { class: "m-me" }, cap);
    chatBox.append(st.answerEl);
    scrollEnd();
    st.rec = new Recorder(st.stream, {
      lang: lang(), captions: await isDev(), silenceSec: st.s.limits.silence_prompt_sec,
      onCaption: (t) => { if (st.answerEl) { st.answerEl.textContent = t; scrollEnd(); } },
      onSilence: () => { if (st.local === "recording") play(st.s.cues.silence_prompt, lang()); },
    });
    st.rec.start();
    setLocal("recording");
    clearInterval(st.recTimer);
    st.recTimer = setInterval(() => {
      const sec = Math.floor((st.rec?.durationMs || 0) / 1000);
      const el = $("#aT");
      if (el) el.textContent = fmtClock(sec);
      if (sec >= st.s.limits.answer_max_sec) finishAnswer("max_duration");
    }, 500);
  }

  async function rerecord() {
    if (st.local !== "recording") return;
    clearInterval(st.recTimer);
    st.rec.cancel();
    st.answerEl?.remove();
    st.answerEl = null;
    const r = await call(() => post(`/interviews/${sid}/attempts/${st.attempt.id}/discard`, { expected_version: st.s.state_version }));
    if (!r) return;
    st.s = r.session;
    setLocal("ready");
    toast("已清除這次錄音，重新回答吧");
  }

  async function finishAnswer(trigger = "user_click") {
    if (st.local !== "recording") return;
    clearInterval(st.recTimer);
    const out = await st.rec.stop();
    st.ackPlaying = play(st.ack, lang());          // 按停止的瞬間就播接話，遮住上傳與追問判斷的時間
    if (st.answerEl && !out.transcript) st.answerEl.textContent = "（正在送出你的回答⋯）";
    await submitAnswer({ ...out, trigger, key: uuid(), attemptId: st.attempt.id, version: st.s.state_version });
  }

  async function submitAnswer(job) {
    setLocal("uploading");
    let uploadId = job.uploadId;
    for (let i = 0; i < 5; i++) {
      try {
        if (!uploadId) uploadId = await uploadFile("answer_audio", job.blob, { attemptId: job.attemptId });
        const r = await api(`/interviews/${sid}/attempts/${job.attemptId}/complete`, { method: "POST", idempotencyKey: job.key, body: {
          upload_id: uploadId, expected_version: job.version, client_duration_ms: job.durationMs, end_trigger: job.trigger,
          client_transcript: job.transcript || null } });
        st.failedUpload = null;
        if (!alive()) return;
        return afterComplete(r);
      } catch (e) {
        if (e.code === "UPLOAD_NOT_FOUND") { uploadId = null; continue; }
        if (e.code === "STATE_CONFLICT") { await sync(); return; }
        if (e.status && e.status < 500 && e.status !== 409 && e.status !== 429) { errorToast(e); break; }
        await new Promise((r) => setTimeout(r, 1000 * 2 ** i));
        if (!alive()) return;
      }
    }
    st.failedUpload = { ...job, uploadId: null };
    setLocal("upload_failed");
  }

  async function afterComplete(r) {
    st.s = r.session;
    if (r.next.type !== "completed") prepare(st.s.current_question);   // 接話還在播，追問語音先開始串流
    if (st.answerEl) st.answerEl.textContent = r.attempt?.quick_transcript || "（已儲存你的回答）";
    st.answerEl = null;
    await st.ackPlaying;
    st.ackPlaying = null;
    await handleNext(r.next);
  }

  async function handleNext(next) {
    if (!alive()) return;
    if (next.type === "completed") return finished(true, next.outro);
    return askCurrent();
  }

  async function skip() {
    if (st.local !== "ready") return;
    const q = st.s.current_question;
    setLocal("speaking");
    chatBox.append(h("div", { class: "m-me skip" }, q.kind === "followup" ? "（跳過這個追問）" : "（跳過這題）"));
    let r;
    try { r = await call(() => post(`/interviews/${sid}/questions/${q.id}/skip`, { expected_version: st.s.state_version })); }
    catch (e) { errorToast(e); setLocal("ready"); return; }
    if (!r) return;
    st.s = r.session;
    if (r.next.type !== "completed") {
      prepare(st.s.current_question);
      const m = aiMsg(null, st.s.cues.skip_ack.text);
      const typing = m.type();
      await play(st.s.cues.skip_ack, lang());
      await typing;
    }
    await handleNext(r.next);
  }

  // ---------- 結束 ----------
  async function finished(live, outro) {
    clearInterval(st.clock);
    clearInterval(st.hb);
    head();
    const s = st.s;
    if (live && outro) {
      setLocal("speaking");
      const text = s.status === "completed" ? outro.text : "今天的面試到這裡結束。這次沒有回答的題目，所以不會產生報告。";
      const m = aiMsg(null, text);
      const typing = m.type();
      await play(s.status === "completed" ? outro : { text }, lang());
      await typing;
    }
    await loadMe(); renderSide();
    if (s.status === "completed") reportCard();
    setLocal("done");
  }

  function reportCard() {
    const sub = h("span", { class: "faint" }, "Ava 正在逐題評分，完成後會通知你");
    const tail = h("span", null, dots());
    chatBox.append(h("a", { class: "rcard", href: `#report/${sid}` }, h("span", { class: "ic" }, icon("report")),
      h("span", { style: "flex:1;min-width:0" }, h("b", null, `面試報告・${st.s.job.company_name}`), sub), tail));
    scrollEnd();
    st.reportStop = sse(`/interviews/${sid}/report/events`, (ev, data) => {
      if (ev === "report_ready" || (ev === "snapshot" && data.status === "ready")) {
        st.reportReady = true;
        sub.textContent = `總分 ${data.overall_score ?? ""}・逐題評分與改善建議`;
        replace(tail, h("span", { style: "color:var(--ink-3)" }, icon("right", 18)));
        if (alive()) { toast("報告完成了"); drawDock(); }
      }
    });
  }

  async function pause() {
    if (!st.s || st.s.status !== "in_progress") return;
    stopAll();
    if (st.local === "recording") { clearInterval(st.recTimer); st.rec.cancel(); st.answerEl?.remove(); st.answerEl = null; }
    try {
      const r = await post(`/interviews/${sid}/pause`, { expected_version: st.s.state_version, reason: "user" });
      st.s = r.session;
    } catch (e) { if (e.code !== "STATE_CONFLICT") errorToast(e); }
    setLocal("paused");
    await loadMe();
    openModal({ cls: "sm", bodyStyle: "text-align:center;padding:32px 24px 26px", body: [
      h("h2", { style: "font-size:1.2rem;font-weight:600" }, "已暫停"),
      h("p", { class: "muted", style: "margin-top:8px" }, "計時已停止。可以先離開，24 小時內都能從「新面試」回來接著練。"),
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
      if (e.code === "STATE_CONFLICT") { await sync(); return; }
      errorToast(e); return;
    }
    await loadMe();
    head();
    return continueFromPhase();
  }

  async function endInterview() {
    const s = st.s;
    const answered = s.progress.answered;
    const ok = await confirmBox({ title: "結束這場面試？",
      text: answered ? `已回答的 ${answered} 題會產生報告，還沒回答的 ${s.progress.total_main - answered} 題不會評分。` : "你還沒有回答任何題目，結束後不會產生報告。",
      ok: answered ? "結束並產生報告" : "結束面試", cancel: "繼續面試" });
    if (!ok) return;
    stopAll();
    if (st.local === "recording") { clearInterval(st.recTimer); st.rec.cancel(); }
    try {
      const r = await post(`/interviews/${sid}/end`, { expected_version: null, reason: "user_ended" });
      st.s = r.session;
      st.ended = true;
      await loadMe();
      if (r.report) { location.hash = `#report/${sid}`; toast("面試已結束，Ava 正在評分"); }
      else { location.hash = "#new"; toast("面試已結束，沒有回答的題目不會產生報告"); }
    } catch (e) { errorToast(e); }
  }

  // ---------- 啟動 ----------
  await isDev();
  head();
  drawDock();
  sync();

  // 離開頁面：暫停（不是作廢），24 小時內可回來繼續
  const onHide = () => {
    if (document.visibilityState === "hidden" && st.local === "recording") st.events.push({ type: "page_hidden_while_recording", at: new Date().toISOString() });
  };
  document.addEventListener("visibilitychange", onHide);
  const onUnload = () => {
    if (st.s?.status === "in_progress") api(`/interviews/${sid}/pause`, { method: "POST", body: { reason: "page_leave" }, keepalive: true }).catch(() => {});
  };
  window.addEventListener("pagehide", onUnload);

  return async () => {
    st.left = true;
    document.removeEventListener("visibilitychange", onHide);
    window.removeEventListener("pagehide", onUnload);
    clearInterval(st.clock); clearInterval(st.hb); clearInterval(st.recTimer);
    stopAll();
    st.reportStop?.();
    try { st.rec?.cancel(); } catch { /* ignore */ }
    st.stream?.getTracks().forEach((t) => t.stop());
    dock.hidden = true;
    clear(dock);
    if (st.s?.status === "in_progress" && !st.ended) {
      try { await post(`/interviews/${sid}/pause`, { reason: "page_leave" }); toast("面試已暫停，24 小時內都能回來繼續"); } catch { /* ignore */ }
    }
    await loadMe().catch(() => {});
  };
}
