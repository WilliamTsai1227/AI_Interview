// GPT-Live 連線：WebRTC（麥克風上行、Ava 語音下行）＋ data channel "oai-events"。
// SDP 交換交給後端代理（API 金鑰不給前端）；雙方字幕依「誰在說話」整理成一段一段的發言，交給呼叫端存檔。
// 事件名稱見 OpenAI Live 文件：session.started / *_transcript.delta / response.event / session.closed。
import { uuid } from "../api.js";

const CLOSE_WAIT_MS = 5000;
const ICE_WAIT_MS = 3000;

export class LiveCall {
  /**
   * @param {MediaStream} stream 麥克風
   * @param {object} hooks onState(state) / onAudio(stream) / onDelta(turn) / onTurn(turn) / onTool(name, args) / onClosed(info) / onEvent(ev)
   *   turn = { seq, role: "interviewer"|"candidate", text, started_ms, ended_ms }
   */
  constructor(stream, hooks = {}, nextSeq = 0) {
    this.stream = stream;
    this.hooks = hooks;
    this.seq = nextSeq;
    this.cur = null;
    this.pc = null;
    this.dc = null;
    this.audio = null;
    this.closed = null;          // session.closed 的 Promise
    this.muted = false;
  }

  async connect(exchange) {
    const pc = new RTCPeerConnection();
    this.pc = pc;
    this.audio = new Audio();
    this.audio.autoplay = true;
    pc.addEventListener("track", (e) => {
      const remote = new MediaStream([e.track]);
      this.audio.srcObject = remote;
      this.audio.play().catch(() => {});
      this.hooks.onAudio?.(remote);       // 給 Ava 的人像對嘴型
    });
    pc.addEventListener("connectionstatechange", () => {
      if (["failed", "disconnected"].includes(pc.connectionState)) this.hooks.onState?.("lost");
    });
    this.stream.getAudioTracks().forEach((t) => { t.enabled = true; pc.addTrack(t, this.stream); });
    const dc = pc.createDataChannel("oai-events");          // 要在 createOffer 之前建立
    this.dc = dc;
    let resolveClosed;
    this.closed = new Promise((r) => { resolveClosed = r; });
    dc.addEventListener("message", ({ data }) => {
      let ev;
      try { ev = JSON.parse(data); } catch { return; }
      this.handle(ev, resolveClosed);
    });
    dc.addEventListener("close", () => { this.flush(); resolveClosed({ reason: "channel_closed", seconds: null }); });
    await pc.setLocalDescription(await pc.createOffer());
    await waitIce(pc);
    const answer = await exchange(pc.localDescription.sdp);
    await pc.setRemoteDescription({ type: "answer", sdp: answer });
    this.hooks.onState?.("connecting");
  }

  handle(ev, resolveClosed) {
    switch (ev.type) {
      case "session.started":
      case "session.resumed":
        this.hooks.onState?.("live"); break;
      case "session.input_transcript.delta":
        this.append("candidate", ev); break;
      case "session.output_transcript.delta":
        this.append("interviewer", ev); break;
      case "session.delegation.created":
        // 沒有設定委派模型（client 模式）：面試不需要外部工具，請它直接繼續
        if (ev.delegation?.target === "client") {
          this.send({ type: "session.commentary.append", delegation_id: ev.delegation.id, content: "這場面試沒有外部工具可用，請直接繼續面試。" });
        }
        break;
      case "response.event": {
        const inner = ev.event || {};
        const item = inner.item;
        if (inner.type === "response.output_item.done" && item?.type === "function_call") {
          let args = {};
          try { args = JSON.parse(item.arguments || "{}"); } catch { /* ignore */ }
          this.hooks.onTool?.(item.name, args);
          this.send({ type: "response.item.create", item: { type: "function_call_output", call_id: item.call_id, output: JSON.stringify({ ok: true }) } });
          this.send({ type: "response.create" });
        }
        break;
      }
      case "session.closed":
        this.flush();
        resolveClosed({ reason: ev.reason || "closed", seconds: ev.usage?.seconds ?? null });
        this.hooks.onClosed?.(ev);
        break;
      case "error":
        this.hooks.onEvent?.({ type: "live_error", code: ev.error?.code, message: String(ev.error?.message || "").slice(0, 200) });
        break;
      default:
    }
  }

  /** 字幕片段：換人說話就把上一段收尾。 */
  append(role, ev) {
    if (!ev.delta) return;
    if (this.cur && this.cur.role !== role) this.flush();
    if (!this.cur) this.cur = { seq: this.seq++, role, text: "", started_ms: ev.start_ms ?? null, ended_ms: null };
    this.cur.text += ev.delta;
    if (ev.end_ms != null) this.cur.ended_ms = ev.end_ms;
    this.hooks.onState?.(role === "interviewer" ? "ava" : "you");
    this.hooks.onDelta?.(this.cur);
  }

  flush() {
    const t = this.cur;
    this.cur = null;
    if (t && t.text.trim()) this.hooks.onTurn?.({ ...t, text: t.text.trim() });
  }

  send(ev) {
    if (this.dc?.readyState === "open") this.dc.send(JSON.stringify({ event_id: uuid(), ...ev }));
  }

  /** 對談中補充給 Ava 的指示（例如時間快到了）。 */
  instruct(text) { this.send({ type: "session.instructions.append", delegation_id: null, content: text }); }

  mute(on) {
    this.muted = on;
    this.send({ type: on ? "session.input_audio.mute" : "session.input_audio.unmute" });
    this.stream.getAudioTracks().forEach((t) => { t.enabled = !on; });
  }

  /** 正常關閉：送 session.close，等 session.closed（拿到計費秒數），逾時就直接斷線。 */
  async close() {
    if (!this.pc) return { reason: "not_connected", seconds: null };
    this.send({ type: "session.close" });
    const info = await Promise.race([this.closed, new Promise((r) => setTimeout(() => r({ reason: "timeout", seconds: null }), CLOSE_WAIT_MS))]);
    this.flush();
    this.teardown();
    return info;
  }

  teardown() {
    try { this.dc?.close(); } catch { /* ignore */ }
    try { this.pc?.close(); } catch { /* ignore */ }
    if (this.audio) { this.audio.srcObject = null; this.audio = null; }
    this.pc = null;
    this.dc = null;
  }
}

function waitIce(pc) {
  if (pc.iceGatheringState === "complete") return Promise.resolve();
  return new Promise((resolve) => {
    const done = () => { pc.removeEventListener("icegatheringstatechange", check); resolve(); };
    const check = () => { if (pc.iceGatheringState === "complete") done(); };
    pc.addEventListener("icegatheringstatechange", check);
    setTimeout(done, ICE_WAIT_MS);
  });
}
