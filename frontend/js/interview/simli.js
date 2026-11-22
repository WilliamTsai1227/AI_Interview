// 面試官的擬真人像（Simli）：後端換 session token → 瀏覽器用 WebRTC（P2P）收 Simli 的影像與「對好嘴的聲音」。
// Ava 的聲音（Live 的 GPT-Live 音軌、依題目的 TTS 音檔）經 AudioWorklet 轉成 PCM16 送給 Simli，
// 原本的播放改成靜音，改由 Simli 的 <audio> 播出，畫面和聲音才會同步。
// 介面和頭貼（avatar.js）一樣：el / setState / destroy；另外有 start、attach（音軌）、route（<audio>）、interrupt。
import { post } from "../api.js";
import { h } from "../dom.js";

const START_TIMEOUT_MS = 12000;

export class SimliAvatar {
  constructor() {
    this.video = h("video", { autoplay: true, playsinline: true, "aria-label": "面試官 Ava" });
    this.video.muted = true;                // 聲音走下面的 <audio>
    this.audio = h("audio", { autoplay: true });
    this.el = h("div", { class: "simli-box" }, this.video, this.audio);
    this.client = null;
    this.ctx = null;
    this.mix = null;
    this.remote = null;
    this.live = false;          // 第一個畫面出來之後才送聲音
    this.closed = false;
    this.onFail = null;
  }

  /** 連上 Simli；失敗或逾時會丟出錯誤（呼叫端改用頭貼）。 */
  async start(sid) {
    const r = await post(`/interviews/${sid}/avatar/connect`, {});
    const { SimliClient, LogLevel } = await import("../vendor/simli-client.js");
    if (this.closed) throw new Error("closed");
    this.ctx = new AudioContext();
    await this.ctx.audioWorklet.addModule("/js/interview/pcm-worklet.js");
    this.mix = this.ctx.createGain();
    const pcm = new AudioWorkletNode(this.ctx, "pcm16");
    const sink = this.ctx.createGain();
    sink.gain.value = 0;                    // worklet 要接到輸出才會持續運作；音量 0，不會出聲
    this.mix.connect(pcm).connect(sink).connect(this.ctx.destination);
    pcm.port.onmessage = (e) => {
      if (!this.live || this.closed) return;
      try { this.client.sendAudioData(new Uint8Array(e.data)); } catch { /* 連線中斷：由 stop／error 事件處理 */ }
    };
    this.ctx.resume().catch(() => {});

    this.client = new SimliClient(r.session_token, this.video, this.audio, r.ice_servers, LogLevel.ERROR, "p2p");
    this.client.on("stop", () => this.fail());
    this.client.on("error", () => this.fail());
    let timer;
    await Promise.race([
      this.client.start(),
      new Promise((_, reject) => { timer = setTimeout(() => reject(new Error("simli timeout")), START_TIMEOUT_MS); }),
    ]).finally(() => clearTimeout(timer));
    this.live = true;
  }

  /** Live：GPT-Live 的音軌。 */
  attach(stream) {
    if (!this.mix) return false;
    try { this.remote?.disconnect(); } catch { /* ignore */ }
    this.remote = this.ctx.createMediaStreamSource(stream);
    this.remote.connect(this.mix);
    return true;
  }

  /** 依題目：TTS 的 <audio>。接進來之後這個元素不再直接出聲（由 Simli 播出）。 */
  route(audioEl) {
    if (!this.mix) return false;
    this.ctx.createMediaElementSource(audioEl).connect(this.mix);
    return true;
  }

  /** 使用者插話：丟掉還沒播的嘴型與聲音。 */
  interrupt() {
    try { this.client?.ClearBuffer(); } catch { /* ignore */ }
  }

  setState() { /* 表情由 Simli 依聲音產生 */ }

  fail() {
    if (this.closed) return;
    this.onFail?.();
  }

  destroy() {
    this.closed = true;
    this.live = false;
    this.client?.stop().catch(() => {});
    this.ctx?.close().catch(() => {});
    this.client = null;
    this.ctx = null;
    this.mix = null;
  }
}
