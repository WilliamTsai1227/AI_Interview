"""Follow-up Writer（fixed：一定寫一題）、Follow-up Decider（adaptive：判斷要不要追問）。

次數一律由程式計數（interviews/service.py），這裡只負責內容。逾時由呼叫端處理。
"""
import json

from pydantic import BaseModel, Field

from app.ai import client, text

PV_WRITER = "fu_writer_v1"
PV_DECIDER = "fu_decider_v1"

GENERIC = {
    "zh": ["可以再具體說明你在這件事裡負責的部分嗎？", "這個結果你是怎麼衡量的？", "如果重來一次，你會有什麼不同的做法？"],
    "en": ["Could you be more specific about the part you owned?", "How did you measure that result?",
           "If you could do it again, what would you do differently?"],
}
PERSONA = {"warm": "溫和、鼓勵", "real": "像真實面試一樣專業", "tough": "直接、追問細節與數據"}


def generic_followup(language: str, asked: int, previous: list[str]) -> str:
    bank = GENERIC.get(language if language in GENERIC else "zh")
    for i in range(len(bank)):
        q = bank[(asked + i) % len(bank)]
        if q not in previous:
            return q
    return bank[asked % len(bank)]


class WriterOut(BaseModel):
    question: str = Field(description="一句追問，≤ 80 字")
    reason: str


class DeciderOut(BaseModel):
    should_follow_up: bool
    question: str = Field(description="追問內容；不追問時為空字串")
    reason: str


def _context(main: dict, transcript: str, previous: list[dict], persona: str, language: str) -> str:
    return json.dumps({
        "主題目": main["text"], "評分規準": (main.get("rubric") or {}).get("must_mention", []),
        "考生剛才的回答（快速轉錄）": transcript or "（沒有聽清楚）",
        "前面的追問與回答": previous, "面試官風格": PERSONA.get(persona, persona), "語言": language,
    }, ensure_ascii=False)


def _ok(q: str, previous: list[dict]) -> bool:
    q = q.strip()
    return 4 <= len(q) <= 120 and not text.is_duplicate(q, [p["question"] for p in previous], 0.7)


async def write(main: dict, transcript: str, previous: list[dict], *, persona: str, language: str,
                user_id=None, session_id=None) -> str | None:
    """回傳追問；內容不合格回傳 None（呼叫端改用通用追問，次數不變）。"""
    def fake() -> WriterOut:
        hint = transcript.strip()[:14]
        q = (f"你剛提到「{hint}」，可以再說明你為什麼這樣做嗎？" if hint and "無法轉錄" not in hint
             else generic_followup(language, len(previous), [p["question"] for p in previous]))
        return WriterOut(question=q, reason="延伸考生的回答")
    out = await client.structured(
        purpose="followup", schema=WriterOut, tier="fast", prompt_version=PV_WRITER, priority="interview",
        timeout=settings_timeout(), attempts=1, user_id=user_id, session_id=session_id, fake=fake,
        system="你是面試官。根據考生剛才的回答寫「一句」追問：針對回答中模糊、缺少細節或值得深挖的地方；不要提出新主題、不要評論對錯、不要重複前面的追問。",
        user=_context(main, transcript, previous, persona, language))
    return out.question.strip() if _ok(out.question, previous) else None


async def decide(main: dict, transcript: str, previous: list[dict], *, persona: str, language: str,
                 user_id=None, session_id=None) -> str | None:
    """adaptive：需要追問時回傳追問內容，否則 None。"""
    def fake() -> DeciderOut:
        short = len(transcript.strip()) < 40
        return DeciderOut(should_follow_up=short or persona == "tough",
                          question=generic_followup(language, len(previous), [p["question"] for p in previous]),
                          reason="回答較短" if short else "風格要求追問")
    out = await client.structured(
        purpose="followup", schema=DeciderOut, tier="fast", prompt_version=PV_DECIDER, priority="interview",
        timeout=settings_timeout(), attempts=1, user_id=user_id, session_id=session_id, fake=fake,
        system="你是面試官。判斷考生的回答是否需要追問：回答過短、離題、缺少評分規準中的關鍵要點或細節時才追問。需要時寫「一句」追問，不提出新主題。",
        user=_context(main, transcript, previous, persona, language))
    if not out.should_follow_up or not _ok(out.question, previous):
        return None
    return out.question.strip()


def settings_timeout() -> float:
    from app.core.config import settings
    return settings.followup_timeout_sec
