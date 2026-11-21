// 面試官 Ava 的即時人像：瀏覽器內用 SVG 畫，嘴型跟著 Ava 的聲音動（Live 讀 WebRTC 音軌的音量；
// 依題目模式的 TTS 沒有音軌可讀，說話時用節奏模擬）。眨眼、呼吸、微微轉頭讓畫面像視訊而不是靜態圖。
// 之後要換成真人影像（串流數位人服務或自架的對嘴模型）時，只要提供相同的 el / setState / attach / destroy 即可。

const NS = "http://www.w3.org/2000/svg";
const SKIN = "#f0c6aa", SKIN_D = "#d9a587", HAIR = "#2b1e18", SUIT = "#2c3547", SHIRT = "#ece7df", LIP = "#b45d58";

function s(tag, attrs = {}, ...kids) {
  const el = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, String(v));
  kids.flat().forEach((k) => k && el.appendChild(k));
  return el;
}

export class Avatar {
  constructor() {
    this.state = "idle";        // idle | speaking | listening | thinking
    this.level = 0;             // 0–1 嘴巴張開程度（平滑後）
    this.analyser = null;
    this.ctx = null;
    this.buf = null;
    this.nextBlink = performance.now() + 2500;
    this.blinkUntil = 0;
    this.nodUntil = 0;
    this.raf = 0;
    this.build();
    this.loop = this.loop.bind(this);
    this.raf = requestAnimationFrame(this.loop);
  }

  build() {
    this.eyes = [s("ellipse", { cx: 174, cy: 182, rx: 6.5, ry: 7.5, fill: "#231a17" }), s("ellipse", { cx: 226, cy: 182, rx: 6.5, ry: 7.5, fill: "#231a17" })];
    this.shine = [s("circle", { cx: 176, cy: 179, r: 1.8, fill: "#fff" }), s("circle", { cx: 228, cy: 179, r: 1.8, fill: "#fff" })];
    this.brows = s("g", { stroke: HAIR, "stroke-width": 4, "stroke-linecap": "round", fill: "none" },
      s("path", { d: "M160 163 Q173 156 186 161" }), s("path", { d: "M214 161 Q227 156 240 163" }));
    this.mouth = s("ellipse", { cx: 200, cy: 232, rx: 14, ry: 1.5, fill: "#6e2a2a" });
    this.teeth = s("rect", { x: 189, y: 228, width: 22, height: 4, rx: 2, fill: "#f6f1ea", opacity: 0 });
    this.smile = s("path", { d: "M185 230 Q200 239 215 230", stroke: LIP, "stroke-width": 3.2, fill: "none", "stroke-linecap": "round" });
    this.head = s("g", {},
      s("path", { d: "M116 176 C106 236 112 276 136 286 C150 291 162 284 168 270 L232 270 C238 284 250 291 264 286 C288 276 294 236 284 176 Z", fill: HAIR }),
      s("ellipse", { cx: 131, cy: 190, rx: 10, ry: 16, fill: SKIN_D }),
      s("ellipse", { cx: 269, cy: 190, rx: 10, ry: 16, fill: SKIN_D }),
      s("ellipse", { cx: 200, cy: 182, rx: 70, ry: 85, fill: SKIN }),
      s("path", { d: "M126 168 C122 104 168 80 204 84 C248 88 282 116 274 170 C258 134 222 118 186 122 C160 126 140 142 126 168 Z", fill: HAIR }),
      this.brows,
      ...this.eyes, ...this.shine,
      s("path", { d: "M201 188 Q195 208 199 213 Q204 215 208 211", stroke: SKIN_D, "stroke-width": 2.6, fill: "none", "stroke-linecap": "round" }),
      s("ellipse", { cx: 160, cy: 212, rx: 13, ry: 7, fill: "#ee9d93", opacity: 0.32 }),
      s("ellipse", { cx: 240, cy: 212, rx: 13, ry: 7, fill: "#ee9d93", opacity: 0.32 }),
      this.mouth, this.teeth, this.smile);
    this.body = s("g", {},
      s("path", { d: "M60 400 C70 322 134 292 200 292 C266 292 330 322 340 400 Z", fill: SUIT }),
      s("path", { d: "M168 294 L200 352 L232 294 Z", fill: SHIRT }),
      s("path", { d: "M168 294 L186 342 L200 352 M232 294 L214 342 L200 352", stroke: "#1f2636", "stroke-width": 3, fill: "none" }));
    const neck = s("rect", { x: 179, y: 238, width: 42, height: 64, rx: 18, fill: SKIN_D });
    this.el = s("svg", { viewBox: "0 0 400 400", class: "ava-svg", role: "img", "aria-label": "面試官 Ava" }, this.body, neck, this.head);
  }

  setState(state) { this.state = state; }

  /** Ava 的聲音（WebRTC 遠端音軌）：用音量驅動嘴型。 */
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
      // 沒有音軌可讀：說話時用兩個不同頻率的波疊出自然的開合節奏
      target = this.state === "speaking" ? Math.max(0, 0.45 + 0.35 * Math.sin(t * 13) + 0.25 * Math.sin(t * 7.3 + 1)) : 0;
    }
    this.level += (Math.min(1, target) - this.level) * (target > this.level ? 0.55 : 0.25);
    const o = this.level;
    this.mouth.setAttribute("ry", (1.5 + o * 11).toFixed(2));
    this.mouth.setAttribute("rx", (14 - o * 3).toFixed(2));
    this.teeth.setAttribute("opacity", o > 0.25 ? "0.9" : "0");
    this.smile.setAttribute("opacity", Math.max(0, 1 - o * 3).toFixed(2));

    // 眨眼：每 2.5–6 秒一次，約 0.13 秒
    if (now > this.nextBlink) { this.blinkUntil = now + 130; this.nextBlink = now + 2500 + Math.random() * 3500; }
    const lid = now < this.blinkUntil ? 0.12 : 1;
    const look = this.state === "thinking" ? -4 : 0;          // 思考時眼睛往上看
    this.eyes.forEach((e) => { e.setAttribute("ry", (7.5 * lid).toFixed(2)); e.setAttribute("cy", String(182 + look)); });
    this.shine.forEach((e) => { e.setAttribute("opacity", lid < 1 ? "0" : "1"); e.setAttribute("cy", String(179 + look)); });

    // 聽你說話時偶爾點頭；平時輕微擺頭與呼吸
    if (this.state === "listening" && now > this.nodUntil + 2500 && Math.random() < 0.004) this.nodUntil = now + 700;
    const nod = now < this.nodUntil ? Math.sin(((this.nodUntil - now) / 700) * Math.PI * 2) * 3 : 0;
    const sway = Math.sin(t * 0.6) * 1.6 + (this.state === "speaking" ? Math.sin(t * 2.1) * 1.2 : 0);
    this.brows.setAttribute("transform", `translate(0 ${this.state === "listening" ? -2 : o * -2})`);
    this.head.setAttribute("transform", `translate(0 ${(Math.sin(t * 1.4) * 1.2 + nod).toFixed(2)}) rotate(${sway.toFixed(2)} 200 250)`);
    this.body.setAttribute("transform", `translate(0 ${(Math.sin(t * 1.4) * 1.5).toFixed(2)})`);
  }

  destroy() {
    cancelAnimationFrame(this.raf);
    this.detach();
  }
}
