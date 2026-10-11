// 播放 Ava 的語音：有 tts_url 就播音檔；開發模式（沒有音檔）改用瀏覽器語音合成；都不行就依字數等待。
// play() 回傳 Promise<{ blocked?: true }>；自動播放被擋時 blocked，由呼叫端顯示「播放」按鈕。

let current = null;   // { stop() }

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
    u.rate = 1.05;
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

export async function play(speech, lang = "zh") {
  if (!speech?.text) return {};
  stopAll();
  if (speech.tts_url) {
    const audio = new Audio(speech.tts_url);
    const ended = new Promise((resolve) => {
      audio.onended = () => resolve({});
      audio.onerror = () => resolve({ error: true });
      current = { stop: () => { audio.pause(); resolve({}); } };
    });
    try {
      await audio.play();
    } catch (e) {
      if (e.name === "NotAllowedError") return { blocked: true };
      return speakBrowser(speech.text, lang);
    }
    const r = await ended;
    return r.error ? speakBrowser(speech.text, lang) : r;
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
