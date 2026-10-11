"""Answer Evaluator（主題目與每個追問各自評分）、Report Aggregator（分數由程式算，模型只寫評語）。"""
import json

from pydantic import BaseModel, Field

from app.ai import client, text

PV_EVAL = "eval_v1"
PV_REPORT = "report_v1"
DIM_KEYS = ["structure", "depth", "fluency", "job_fit", "confidence"]


class Dimensions(BaseModel):
    structure: int
    depth: int
    fluency: int
    job_fit: int
    confidence: int


class EvalOut(BaseModel):
    score: int = Field(description="0–100")
    dimensions: Dimensions
    evidence: list[str] = Field(description="逐字稿中的原句，必須一字不差")
    strengths: list[str] = Field(description="做得好的地方，2–3 點")
    improvements: list[str] = Field(description="改善建議，2–3 點，具體可執行")
    better_answer: str = Field(description="「可以這樣說」：示範更好的口語回答，≤ 200 字")
    highlight: str = Field(description="逐字稿中最需要注意的一句（原句），沒有就空字串")


EVAL_SYSTEM = """你是嚴謹但友善的面試教練，評估考生「這一題」的口語回答（繁體中文回饋）。
- 只根據這一題的回答評分；若是追問，主題目與前面的回答只作為上下文。
- evidence 與 highlight 必須是逐字稿中一字不差的原句。
- improvements 要具體可執行；better_answer 以考生的經驗為基礎改寫，口語、自然。"""


def _clamp(v: int) -> int:
    return max(0, min(100, int(v)))


def _fake_eval(question: str, transcript: str, language: str) -> EvalOut:
    t = transcript.strip()
    n = len(t)
    fillers, _ = text.filler_stats(t, language)
    has_num = any(c.isdigit() for c in t)
    base = 55 + min(25, n // 12) + (8 if has_num else 0) - min(15, fillers * 2)
    s = _clamp(base)
    first = next((x for x in t.replace("！", "。").replace("？", "。").split("。") if x.strip()), "")[:40]
    return EvalOut(
        score=s, dimensions=Dimensions(structure=_clamp(s + 2), depth=_clamp(s - 3 + (6 if has_num else 0)),
                                       fluency=_clamp(s - fillers * 3), job_fit=_clamp(s + 4), confidence=_clamp(s - 2)),
        evidence=[first] if first else [],
        strengths=(["有提到具體數據，很有說服力"] if has_num else []) + (["回答有完整說明情境"] if n > 80 else ["回答簡潔"]),
        improvements=(["補上可量化的結果，例如時間、比例或金額"] if not has_num else [])
        + (["回答偏短，可以用「情境 → 做法 → 結果」補充細節"] if n < 80 else [])
        + (["減少「嗯」「然後」等贅詞，讓回答聽起來更肯定"] if fillers > 2 else ["結尾補一句這次經驗帶給你的改變"]),
        better_answer=f"可以先用一句話點出結論，再說明情境、你的做法和結果。例如：「{first or '我會先釐清目標'}……最後結果是……，這讓我學到……。」",
        highlight=first)


class EvalResult(BaseModel):
    out: EvalOut
    needs_review: bool
    review_reason: str | None


async def evaluate(*, question: str, rubric: dict | None, transcript: str, job: dict, resume: dict | None,
                   context: list[dict], language: str, duration_ms: int | None, user_id=None,
                   session_id=None) -> EvalResult:
    t = (transcript or "").strip()
    if len(t) < 5:
        empty = EvalOut(score=0, dimensions=Dimensions(structure=0, depth=0, fluency=0, job_fit=0, confidence=0),
                        evidence=[], strengths=[], improvements=["錄音幾乎沒有內容，重新回答一次這題吧"],
                        better_answer="", highlight="")
        return EvalResult(out=empty, needs_review=True, review_reason="錄音幾乎沒有聲音或內容")
    user = json.dumps({"題目": question, "評分規準": rubric or {}, "上下文（主題目與前面的追問）": context,
                       "職缺摘要": {k: job.get(k) for k in ("company_name", "title", "requirements")},
                       "履歷背景": resume or "（無）", "考生回答逐字稿": t, "回答秒數": (duration_ms or 0) // 1000},
                      ensure_ascii=False)
    out = await client.structured(purpose="evaluate", schema=EvalOut, system=EVAL_SYSTEM, user=user,
                                  tier="reasoning", prompt_version=PV_EVAL, user_id=user_id, session_id=session_id,
                                  fake=lambda: _fake_eval(question, t, language))
    out.score = _clamp(out.score)
    for k in DIM_KEYS:
        setattr(out.dimensions, k, _clamp(getattr(out.dimensions, k)))
    out.evidence = [e for e in out.evidence if e and e in t]       # 證據原句必須出現在逐字稿中
    if out.highlight and out.highlight not in t:
        out.highlight = ""
    return EvalResult(out=out, needs_review=False, review_reason=None)


class ReportOut(BaseModel):
    summary: str = Field(description="總結評語，2–3 句，先講最大優勢，再講最需要改善的一點")
    focus_areas: list[str] = Field(description="下次練習優先順序，2–3 點")


async def aggregate(*, items: list[dict], overall: int, user_id=None, session_id=None) -> ReportOut:
    def fake() -> ReportOut:
        low = sorted(items, key=lambda x: x["score"])[:2]
        return ReportOut(
            summary=f"這場整體 {overall} 分。" + ("你的回答有具體內容，這是最大的優勢。" if overall >= 70 else "回答的方向正確，但細節還不夠。")
            + "下次可以多用「情境 → 做法 → 結果」的結構，並補上可量化的數字。",
            focus_areas=[f"再練一次「{x['category']}」題：{x['improvement']}" for x in low if x.get("improvement")]
            or ["用 STAR 結構回答行為題", "每題補上一個可量化的結果"])
    return await client.structured(
        purpose="report", schema=ReportOut, tier="fast", prompt_version=PV_REPORT, user_id=user_id,
        session_id=session_id, fake=fake,
        system="你是面試教練。根據各題評分，寫出整場面試的總結評語與下次練習的優先順序（繁體中文、具體）。不要計算或改寫分數。",
        user=json.dumps({"總分": overall, "各題": items}, ensure_ascii=False))
