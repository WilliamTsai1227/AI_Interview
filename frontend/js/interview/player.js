// 播放 Ava 的語音：有 tts_url 就播音檔；開發模式（沒有音檔）改用瀏覽器語音合成；都不行就依字數等待。
// play() 回傳 Promise<{ blocked?: true }>；自動播放被擋時 blocked，由呼叫端顯示「播放」按鈕。

let current = null;   // { stop() }
let rate = 1.15;      // Ava 說話速度：TTS 本身偏慢，用播放速率微調（瀏覽器會保持音高）

export function setRate(r) { rate = Number(r) || 1; }

export function stopAll() {
  try { current?.stop(); } catch { /* ignore */ }
  current = null;
  try { window.speechSynthesis?.cancel(); } catch { /* ignore */ }
}

function waitFor(text) {
  return new Promise((resolve) => {
    const t = setTimeout(() => resolve({}), Math.min(8000, 600 + (text || "").length * 120));
    current = { stop: () => { clearTimeout(t); resolve({}); } };
  });
}

function speakBrowser(text, lang) {
  const ss = window.speechSynthesis;
  if (!ss || !window.SpeechSynthesisUtterance) return waitFor(text);
  return new Promise((resolve) => {
    const u = new SpeechSynthesisUtterance(text);
    u.lang = lang === "en" ? "en-US" : "zh-TW";
    u.rate = Math.min(1.5, rate);
    const voice = ss.getVoices().find((v) => v.lang.replace("_", "-").startsWith(u.lang)) || ss.getVoices().find((v) => v.lang.startsWith(u.lang.slice(0, 2)));
    if (voice) u.voice = voice;
    let done = false;
    const finish = () => { if (!done) { done = true; clearTimeout(guard); resolve({}); } };
    const guard = setTimeout(finish, 4000 + text.length * 250);   // 有些瀏覽器不觸發 onend
    u.onend = finish;
    u.onerror = finish;
    current = { stop: () => { ss.cancel(); finish(); } };
    ss.cancel();
    ss.speak(u);
  });
}

const prepared = new Map();   // url → 預先開始下載的 Audio（追問串流：接話播放時就先緩衝）

function makeAudio(src) {
  const a = new Audio(src);
  a.preload = "auto";
  a.playbackRate = rate;
  a.defaultPlaybackRate = rate;
  a.preservesPitch = true;
  return a;
}

/** 預先開始下載（不播放）。 */
export function prepare(speech) {
  const url = speech?.tts_url;
  if (!url || prepared.has(url)) return;
  const a = makeAudio(url);
  a.load();
  prepared.set(url, a);
}

async function playAudio(audio) {
  const ended = new Promise((resolve) => {
    audio.onended = () => resolve({});
    audio.onerror = () => resolve({ error: true });
    current = { stop: () => { audio.pause(); resolve({}); } };
  });
  try {
    audio.playbackRate = rate;
    await audio.play();
  } catch (e) {
    return e.name === "NotAllowedError" ? { blocked: true } : { error: true };
  }
  return ended;
}

export async function play(speech, lang = "zh") {
  if (!speech?.text) return {};
  stopAll();
  const url = speech.tts_url;
  if (url) {
    const audio = prepared.get(url) || makeAudio(url);
    prepared.delete(url);
    let r = await playAudio(audio);
    if (r.blocked || !r.error) return r;
    // 串流播放失敗（例如瀏覽器不支援邊收邊播）：下載完整檔再播一次
    try {
      const res = await fetch(url);
      if (res.ok) {
        const blobUrl = URL.createObjectURL(await res.blob());
        r = await playAudio(makeAudio(blobUrl));
        URL.revokeObjectURL(blobUrl);
        if (!r.error) return r;
      }
    } catch { /* 改用瀏覽器語音 */ }
  }
  return speakBrowser(speech.text, lang);
}

/** 錄音檔播放（報告頁「聽我的錄音」）。 */
export function playUrl(url) {
  stopAll();
  const a = new Audio(url);
  current = { stop: () => a.pause() };
  return a.play();
}
