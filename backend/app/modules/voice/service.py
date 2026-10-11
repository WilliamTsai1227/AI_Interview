"""VoiceProvider（scripted）：TTS 合成與快取、接話短句（cues）、快速轉錄。

voice 不知道題目順序，只負責「把指定內容變成聲音、把錄音變成文字」（architecture.md §5.2）。
開發模式（沒有 OPENAI_API_KEY）tts_url 為 null，前端改用瀏覽器語音合成。
"""
import hashlib
import random

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
PERSONA_STYLE = {"warm": "語氣溫和、友善、放慢一點", "real": "語氣專業、自然", "tough": "語氣直接、節奏明快"}


def cues_lang(language: str) -> str:
    return "en" if language == "en" else "zh"


def intro_text(language: str, short_name: str) -> str:
    if language == "en":
        return f"Hi {short_name}! I'm Ava, your interviewer today. Let's get started."
    return f"你好，{short_name}！我是今天的面試官 Ava。我們開始吧。"


def _hash(text: str, language: str) -> str:
    return hashlib.sha1(f"{settings.tts_model}|{settings.tts_voice}|{language}|{text}".encode()).hexdigest()


async def cached_key(text: str, language: str) -> str | None:
    try:
        return await redis.client().get(f"{redis.P}cache:tts:{_hash(text, language)}")
    except Exception:
        return None


async def synthesize(text: str, language: str, persona: str = "real", *, priority: str = "normal",
                     user_id=None, session_id=None) -> str | None:
    """合成並快取（30 天）；回傳物件 key。開發模式回傳 None。"""
    if not settings.ai_enabled:
        return None
    key = await cached_key(text, language)
    if key:
        return key
    audio = await ai.tts(text, instructions=PERSONA_STYLE.get(persona), priority=priority, user_id=user_id,
                         session_id=session_id)
    if not audio:
        return None
    key = f"tts/{_hash(text, language)}.mp3"
    await storage.put(key, audio, "audio/mpeg")
    try:
        await redis.client().set(f"{redis.P}cache:tts:{_hash(text, language)}", key, ex=30 * 86400)
    except Exception:
        pass
    return key


def url(key: str | None) -> str | None:
    return storage.presign_get(key, 900, "audio/mpeg") if key else None


async def speech(text: str, language: str) -> dict:
    return {"text": text, "tts_url": url(await cached_key(text, language))}


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
