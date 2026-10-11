// 逐題錄音：MediaRecorder（麥克風 track）＋靜音偵測（AnalyserNode）。
// 開發模式（後端沒有 OpenAI 金鑰）另外用瀏覽器語音辨識產生字幕，送出時附在 client_transcript。
import { toast } from "../ui.js";

export async function requestMic() {
  if (!navigator.mediaDevices?.getUserMedia) { toast("這個瀏覽器不支援錄音，請改用 Chrome、Edge 或 Safari"); return null; }
  try {
    return await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
  } catch (e) {
    toast(e.name === "NotAllowedError" ? "需要麥克風權限才能面試，請在網址列允許使用麥克風" : "找不到麥克風，請確認裝置後再試");
    return null;
  }
}

function pickMime() {
  for (const t of ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus"]) {
    if (window.MediaRecorder?.isTypeSupported?.(t)) return t;
  }
  return "";
}

export class Recorder {
  constructor(stream, { lang = "zh", captions = false, onCaption, onSilence, silenceSec = 12, onLevel } = {}) {
    this.stream = stream;
    this.lang = lang;
    this.captions = captions;
    this.onCaption = onCaption;
    this.onSilence = onSilence;
    this.silenceMs = silenceSec * 1000;
    this.onLevel = onLevel;
    this.mime = pickMime();
    this.chunks = [];
    this.finalText = "";
    this.startedAt = 0;
  }

  start() {
    this.chunks = [];
    this.finalText = "";
    this.stream.getAudioTracks().forEach((t) => { t.enabled = true; });
    this.mr = new MediaRecorder(this.stream, this.mime ? { mimeType: this.mime } : undefined);
    this.mr.ondataavailable = (e) => { if (e.data?.size) this.chunks.push(e.data); };
    this.mr.start(1000);
    this.startedAt = performance.now();
    this._monitor();
    if (this.captions) this._recognize();
  }

  get durationMs() { return this.startedAt ? Math.round(performance.now() - this.startedAt) : 0; }

  /** 停止並回傳 { blob, durationMs, transcript }。 */
  stop() {
    return new Promise((resolve) => {
      const durationMs = this.durationMs;
      const done = () => {
        this._cleanup();
        const type = (this.mr?.mimeType || this.mime || "audio/webm").split(";")[0];
        resolve({ blob: new Blob(this.chunks, { type }), durationMs, transcript: this.finalText.trim() });
      };
      if (!this.mr || this.mr.state === "inactive") return done();
      this.mr.onstop = done;
      this.mr.stop();
    });
  }

  cancel() {
    try { if (this.mr && this.mr.state !== "inactive") { this.mr.onstop = null; this.mr.stop(); } } catch { /* ignore */ }
    this.chunks = [];
    this._cleanup();
  }

  _cleanup() {
    this.stream.getAudioTracks().forEach((t) => { t.enabled = false; });
    cancelAnimationFrame(this._raf);
    try { this._ac?.close(); } catch { /* ignore */ }
    this._ac = null;
    try { this._sr?.stop(); } catch { /* ignore */ }
    this._sr = null;
    this.startedAt = 0;
  }

  _monitor() {
    try {
      this._ac = new (window.AudioContext || window.webkitAudioContext)();
      const src = this._ac.createMediaStreamSource(this.stream);
      const an = this._ac.createAnalyser();
      an.fftSize = 1024;
      src.connect(an);
      const buf = new Uint8Array(an.fftSize);
      let lastVoice = performance.now(), prompted = false;
      const tick = () => {
        an.getByteTimeDomainData(buf);
        let sum = 0;
        for (const v of buf) sum += (v - 128) ** 2;
        const rms = Math.sqrt(sum / buf.length) / 128;
        this.onLevel?.(rms);
        const now = performance.now();
        if (rms > 0.03) { lastVoice = now; prompted = false; }
        if (!prompted && now - lastVoice > this.silenceMs) { prompted = true; this.onSilence?.(); }
        this._raf = requestAnimationFrame(tick);
      };
      tick();
    } catch { /* 沒有 AudioContext 就不做靜音偵測 */ }
  }

  _recognize() {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) return;
    const sr = new SR();
    sr.lang = this.lang === "en" ? "en-US" : "zh-TW";
    sr.continuous = true;
    sr.interimResults = true;
    sr.onresult = (e) => {
      let interim = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const r = e.results[i];
        if (r.isFinal) this.finalText += r[0].transcript;
        else interim += r[0].transcript;
      }
      this.onCaption?.(this.finalText + interim);
    };
    sr.onend = () => { if (this._sr === sr && this.startedAt) { try { sr.start(); } catch { /* ignore */ } } };
    sr.onerror = () => {};
    try { sr.start(); this._sr = sr; } catch { /* ignore */ }
  }
}
