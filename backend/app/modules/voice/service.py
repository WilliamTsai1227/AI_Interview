"""VoiceProvider（scripted）：TTS 合成與快取、接話短句（cues）、快速轉錄。

voice 不知道題目順序，只負責「把指定內容變成聲音、把錄音變成文字」（architecture.md §5.2）。
開發模式（沒有 OPENAI_API_KEY）tts_url 為 null，前端改用瀏覽器語音合成。
"""
import hashlib
import hmac
import random
import time

import structlog

from app.ai import client as ai
from app.core import redis, storage
from app.core.config import settings

log = structlog.get_logger()

CUES = {
    "zh": {"acks": ["嗯，了解。", "謝謝你的分享。", "好，我明白了。", "這個例子很具體。"],
           "last_question_ack": "好的，我們來聊最後一題。", "silence_prompt": "這題還有要補充的嗎？",
           "skip_ack": "沒關係，我們換下一題。", "followup_lead": "我想再多問一點。",
           "outro": "今天的面試到這裡結束，謝謝你。我會整理每一題的評分和改善建議。"},
    "en": {"acks": ["Okay, got it.", "Thanks for sharing.", "Alright, I see.", "That's a concrete example."],
           "last_question_ack": "Great, let's move on to the last question.",
           "silence_prompt": "Is there anything else you'd like to add?", "skip_ack": "No problem, let's move on.",
           "followup_lead": "I'd like to dig a little deeper.",
           "outro": "That's the end of today's interview. Thank you. I'll put together scores and suggestions for each answer."},
}
# gpt-4o-mini-tts 的說話風格指示（語速無法用參數調，只能靠指示；前端另外用 playbackRate 微調）
STYLE_VERSION = "v2"     # 指示改了就改版本，舊的快取音檔就不會再被使用
BASE_STYLE = {
    "zh": ("你是台灣科技公司的資深面試官 Ava，用台灣口音的自然中文（台灣國語）跟求職者面對面對話。"
           "像真人聊天，不是播音或朗讀：有自然的抑揚頓挫與輕重音，語速自然偏快，句子之間只做短暫停頓。"
           "英文技術名詞（例如 API、Kubernetes、RAG）用自然的英文發音直接念出來。"),
    "en": ("You are Ava, a senior interviewer at a tech company, talking face to face with a candidate. "
           "Sound like a real person in conversation, not a narrator: natural intonation, a brisk natural pace, short pauses."),
}
PERSONA_STYLE = {"warm": "語氣溫暖、友善、帶點鼓勵。", "real": "語氣專業、自然、親切。", "tough": "語氣直接、俐落，節奏明快。"}


def style(language: str, persona: str) -> str:
    return BASE_STYLE[cues_lang(language)] + " " + PERSONA_STYLE.get(persona, PERSONA_STYLE["real"])


def cues_lang(language: str) -> str:
    return "en" if language == "en" else "zh"


def intro_text(language: str, short_name: str) -> str:
    if language == "en":
        return f"Hi {short_name}! I'm Ava, your interviewer today. Let's get started."
    return f"你好，{short_name}！我是今天的面試官 Ava。我們開始吧。"


def _hash(text: str, language: str, persona: str = "real") -> str:
    return hashlib.sha1(
        f"{settings.tts_model}|{settings.tts_voice}|{STYLE_VERSION}|{persona}|{language}|{text}".encode()).hexdigest()


async def cached_key(text: str, language: str, persona: str = "real") -> str | None:
    try:
        return await redis.client().get(f"{redis.P}cache:tts:{_hash(text, language, persona)}")
    except Exception:
        return None


async def synthesize(text: str, language: str, persona: str = "real", *, priority: str = "normal",
                     user_id=None, session_id=None) -> str | None:
    """合成並快取（30 天）；回傳物件 key。開發模式回傳 None。"""
    if not settings.ai_enabled:
        return None
    key = await cached_key(text, language, persona)
    if key:
        return key
    audio = await ai.tts(text, instructions=style(language, persona), priority=priority, user_id=user_id,
                         session_id=session_id)
    if not audio:
        return None
    key = f"tts/{_hash(text, language, persona)}.mp3"
    await storage.put(key, audio, "audio/mpeg")
    try:
        await redis.client().set(f"{redis.P}cache:tts:{_hash(text, language, persona)}", key, ex=30 * 86400)
    except Exception:
        pass
    return key


def url(key: str | None) -> str | None:
    return storage.presign_get(key, 900, "audio/mpeg") if key else None


async def speech(text: str, language: str, persona: str = "real") -> dict:
    return {"text": text, "tts_url": url(await cached_key(text, language, persona))}


async def cues(language: str) -> dict:
    c = CUES[cues_lang(language)]
    return {"acks": [await speech(t, language) for t in c["acks"]],
            "last_question_ack": await speech(c["last_question_ack"], language),
            "silence_prompt": await speech(c["silence_prompt"], language),
            "skip_ack": await speech(c["skip_ack"], language),
            "outro": await speech(c["outro"], language)}


async def warm_cues(language: str) -> None:
    c = CUES[cues_lang(language)]
    for t in c["acks"] + [c["last_question_ack"], c["silence_prompt"], c["skip_ack"], c["outro"]]:
        try:
            await synthesize(t, language)
        except Exception as e:
            log.warning("cue_tts_failed", text=t, error=str(e))


async def pick_ack(language: str, last: bool) -> dict:
    c = CUES[cues_lang(language)]
    text = c["last_question_ack"] if last else random.choice(c["acks"])
    s = await speech(text, language)
    s["realtime_instructions"] = None
    return s


# ---------- 追問串流 ----------
# <audio> 不能帶 Authorization，所以串流網址用短效 HMAC 簽章（只綁這一題、15 分鐘）
STREAM_TTL = 900


def _sign(qid, exp: int) -> str:
    return hmac.new(settings.jwt_secret.encode(), f"tts:{qid}:{exp}".encode(), hashlib.sha256).hexdigest()[:32]


def stream_url(qid) -> str:
    exp = int(time.time()) + STREAM_TTL
    return f"/api/v1/tts/q/{qid}?exp={exp}&sig={_sign(qid, exp)}"


def verify(qid, exp: int, sig: str) -> bool:
    return exp >= time.time() and hmac.compare_digest(_sign(qid, exp), sig or "")


async def stream_question(q, language: str, persona: str, user_id, session_id):
    """把追問語音串流給瀏覽器，同時收集完整音檔；結束後存物件儲存＋快取，之後重播直接讀檔。"""
    buf = bytearray()
    async for chunk in ai.tts_stream(q["text"], instructions=style(language, persona), user_id=user_id, session_id=session_id):
        buf.extend(chunk)
        yield chunk
    if not buf:
        return
    key = f"tts/{_hash(q['text'], language, persona)}.mp3"
    try:
        await storage.put(key, bytes(buf), "audio/mpeg")
        await redis.client().set(f"{redis.P}cache:tts:{_hash(q['text'], language, persona)}", key, ex=30 * 86400)
        from app.core import db
        async with db.connection() as conn:
            await conn.execute("UPDATE session_questions SET tts_audio_key = $2, tts_status = 'ready' WHERE id = $1", q["id"], key)
    except Exception as e:
        log.warning("tts_stream_store_failed", error=str(e))
