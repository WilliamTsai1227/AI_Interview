// AudioWorklet：把 Ava 的聲音轉成 Simli 要的格式（PCM16、16kHz、單聲道），每 3000 個取樣（約 0.19 秒）送回主執行緒一次。
// 從本站載入（CSP 不允許 blob: 程式），所以不用 Simli SDK 內建的 listenTo*。
const OUT_RATE = 16000;
const CHUNK = 3000;

class Pcm16 extends AudioWorkletProcessor {
  constructor() {
    super();
    this.step = sampleRate / OUT_RATE;      // 例如 48kHz → 每 3 個取樣平均成 1 個（簡單的低通，避免疊頻）
    this.phase = 0;
    this.acc = 0;
    this.cnt = 0;
    this.buf = new Int16Array(CHUNK);
    this.n = 0;
  }

  process(inputs) {
    const ch = inputs[0]?.[0];
    const len = ch ? ch.length : 128;       // 沒有輸入時送靜音：Simli 的 handleSilence 關閉時，30 秒沒收到聲音會斷線
    for (let i = 0; i < len; i++) {
      this.acc += ch ? ch[i] : 0;
      this.cnt += 1;
      this.phase += 1;
      if (this.phase < this.step) continue;
      this.phase -= this.step;
      const s = Math.max(-1, Math.min(1, this.acc / this.cnt));
      this.acc = 0;
      this.cnt = 0;
      this.buf[this.n++] = s < 0 ? s * 0x8000 : s * 0x7fff;
      if (this.n === CHUNK) {
        this.port.postMessage(this.buf.buffer, [this.buf.buffer]);
        this.buf = new Int16Array(CHUNK);
        this.n = 0;
      }
    }
    return true;
  }
}

registerProcessor("pcm16", Pcm16);
