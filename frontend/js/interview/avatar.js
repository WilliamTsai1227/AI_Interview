// 面試官 Ava 沒有影像時的畫面：像視訊會議裡對方沒開鏡頭，中間一個圓形頭貼。
// Ava 說話時頭貼外圈會亮起並跟著音量起伏（Live 讀 WebRTC 音軌的音量；依題目模式的 TTS 沒有音軌可讀，說話時用節奏模擬）。
// 擬真人像（simli.js）接上後由 stage.js 換掉這個畫面；介面相同：el / setState / attach / destroy。
import { h } from "../dom.js";

export class Avatar {
  constructor() {
    this.state = "idle";        // idle | speaking | listening | thinking
    this.level = 0;             // 0–1 外圈亮度（平滑後）
    this.analyser = null;
    this.ctx = null;
    this.buf = null;
    this.ring = h("div", { class: "ava-ring" });
    this.el = h("div", { class: "ava-off", role: "img", "aria-label": "面試官 Ava（未開啟鏡頭）" },
      h("div", { class: "ava-pic" }, this.ring, h("div", { class: "ava-face" }, "A")));
    this.loop = this.loop.bind(this);
    this.raf = requestAnimationFrame(this.loop);
  }

  setState(state) {
    this.state = state;
    this.el.dataset.state = state;
  }

  /** Ava 的聲音（WebRTC 遠端音軌）：用音量驅動外圈。 */
  attach(stream) {
    this.detach();
    try {
      this.ctx = new (window.AudioContext || window.webkitAudioContext)();
      const src = this.ctx.createMediaStreamSource(stream);
      this.analyser = this.ctx.createAnalyser();
      this.analyser.fftSize = 512;
      this.buf = new Uint8Array(this.analyser.fftSize);
      src.connect(this.analyser);       // 不接到 destination：聲音仍由 <audio> 播放，這裡只量音量
      this.ctx.resume?.().catch(() => {});
    } catch { this.analyser = null; }
  }

  detach() {
    try { this.ctx?.close(); } catch { /* ignore */ }
    this.ctx = null;
    this.analyser = null;
  }

  volume() {
    if (!this.analyser) return null;
    this.analyser.getByteTimeDomainData(this.buf);
    let sum = 0;
    for (const v of this.buf) sum += (v - 128) * (v - 128);
    return Math.min(1, Math.sqrt(sum / this.buf.length) / 28);
  }

  loop(now) {
    this.raf = requestAnimationFrame(this.loop);
    const t = now / 1000;
    let target = this.volume();
    if (target === null) {
      // 沒有音軌可讀：說話時用兩個不同頻率的波疊出自然的起伏
      target = this.state === "speaking" ? Math.max(0, 0.45 + 0.35 * Math.sin(t * 13) + 0.25 * Math.sin(t * 7.3 + 1)) : 0;
    }
    this.level += (Math.min(1, target) - this.level) * (target > this.level ? 0.55 : 0.2);
    const o = this.level;
    this.ring.style.opacity = Math.min(1, o * 1.6).toFixed(2);
    this.ring.style.transform = `scale(${(1 + o * 0.14).toFixed(3)})`;
  }

  destroy() {
    cancelAnimationFrame(this.raf);
    this.detach();
  }
}
