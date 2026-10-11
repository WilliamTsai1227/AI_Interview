"""Prep Analyzer（每次重讀職缺＋履歷）、Prep Extender（再多產生）。只附加，不刪除。"""
import json

from pydantic import BaseModel, Field

from app.ai import client, text

PV_ANALYZE = "prep_v1"
PV_EXTEND = "prep_more_v1"


class Direction(BaseModel):
    label: str
    probability: int


class Likely(BaseModel):
    question: str
    why: str = Field(description="為什麼會問，≤ 50 字")
    tip: str = Field(description="回答建議，≤ 70 字")
    priority: str = Field(description="high|medium|low")


class AnalyzeOut(BaseModel):
    match_score: int
    verdict_title: str = Field(description="≤ 12 字，例如「整體很適合你」")
    verdict_text: str = Field(description="≤ 50 字的總評")
    strengths: list[str]
    gaps: list[str]
    directions: list[Direction]
    likely_questions: list[Likely]
    checklist: list[str]


ANALYZE_SYSTEM = """你是求職教練。比對「職缺內容」與「履歷」，產生面試建議（繁體中文、口語、具體）。
- strengths、gaps 各 2–4 點，每點 ≤ 25 字；沒有履歷時 strengths 給空陣列，gaps 寫職缺最重要的準備方向。
- directions：高機率會被問的方向 3–5 個，probability 0–100。
- likely_questions：3–5 題「新的」可能題目（不要和既有項目重複）。
- checklist：3–5 項面試前準備事項（不要和既有項目重複）。"""


def _fake_analyze(job: dict, resume: dict | None, existing_q: list[str], existing_c: list[str]) -> AnalyzeOut:
    co = job.get("company_name") or "這間公司"
    reqs = job.get("requirements") or [job.get("title") or "職缺核心能力"]
    skills = set((resume or {}).get("skills") or [])
    tags = job.get("tags") or []
    hit = [t for t in tags if t in skills]
    score = min(95, 60 + 8 * len(hit) + (10 if resume else 0)) if resume else 70
    likely = [Likely(question=f"為什麼想加入 {co}？", why="幾乎每場面試都會問，確認動機與你對公司的了解。",
                     tip="說出一個你對產品的具體觀察，再連到你過去的經驗。", priority="high"),
              Likely(question=f"請分享一個和「{reqs[0]}」相關的經驗。", why="這是職缺條件的第一項，面試官一定會確認。",
                     tip="用 STAR 結構回答，最後補上可量化的結果。", priority="high"),
              Likely(question="你遇過最大的挑戰是什麼？怎麼解決？", why="評估解決問題與抗壓能力。",
                     tip="挑一個有轉折的故事，重點放在你做了什麼決定。", priority="medium")]
    checklist = [f"看一次 {co} 的官網與產品", "準備 2 個要反問面試官的問題", f"對照「{reqs[0]}」準備一個實例"]
    return AnalyzeOut(
        match_score=score, verdict_title="整體很適合你" if score >= 85 else "有機會，要補強幾個重點",
        verdict_text=("先把職缺條件的前兩項各準備一個具體例子，再補強對公司的了解。" if resume
                      else "上傳履歷後可以看到你的優勢與落差。"),
        strengths=([f"具備 {', '.join(hit[:3])} 經驗，符合職缺需求"] if hit else ["履歷內容和職缺方向相近"]) if resume else [],
        gaps=[f"多準備「{r}」的具體例子" for r in reqs[:2]],
        directions=[Direction(label=r[:30], probability=max(40, 90 - i * 12)) for i, r in enumerate(reqs[:4])]
        + [Direction(label="動機與公司了解", probability=60)],
        likely_questions=[q for q in likely if not text.is_duplicate(q.question, existing_q)],
        checklist=[c for c in checklist if not text.is_duplicate(c, existing_c)])


async def analyze(*, job: dict, resume: dict | None, existing_questions: list[str], existing_checklist: list[str],
                  user_id=None) -> AnalyzeOut:
    user = json.dumps({"職缺": {k: job.get(k) for k in ("company_name", "title", "about", "duties", "requirements", "tags", "salary_text")},
                       "履歷": resume or "（沒有履歷）", "既有可能題目": existing_questions,
                       "既有準備清單": existing_checklist}, ensure_ascii=False)
    out = await client.structured(purpose="prep", schema=AnalyzeOut, system=ANALYZE_SYSTEM, user=user,
                                  tier="reasoning", prompt_version=PV_ANALYZE, user_id=user_id,
                                  fake=lambda: _fake_analyze(job, resume, existing_questions, existing_checklist))
    out.match_score = max(0, min(100, out.match_score))
    for d in out.directions:
        d.probability = max(0, min(100, d.probability))
    return out


class LikelyMore(BaseModel):
    items: list[Likely]


class ChecklistMore(BaseModel):
    items: list[str]


_MORE_LIKELY = [
    ("如果時程很趕、需求又一直變，你會怎麼處理？", "評估溝通與排優先順序的能力。", "講你怎麼和 PM 對齊範圍，給一個具體例子。", "medium"),
    ("你怎麼確保程式碼品質？", "了解你的工程習慣與標準。", "提到測試、Code Review 和你推動過的規範。", "medium"),
    ("說一次你犯錯的經驗，之後怎麼改進？", "評估自省與成長。", "坦白說錯在哪，重點放在之後做了什麼改變。", "medium"),
    ("你怎麼和設計師、PM 等不同角色合作？", "職缺需要跨部門協作。", "舉一次意見不同、最後達成共識的例子。", "medium"),
    ("請說明一個你做過的技術取捨。", "資深職位很重視取捨與判斷。", "講清楚選項、評估標準、最後的結果。", "high"),
    ("你怎麼學習新技術？最近學了什麼？", "評估學習能力與好奇心。", "舉一個最近學的東西，並說你怎麼用在工作上。", "low"),
    ("你為什麼想離開現在的工作？", "確認動機，也看你怎麼談前公司。", "聚焦在想追求的成長，不批評前公司。", "medium"),
    ("你希望三年後在這個職位上做到什麼？", "確認長期動機與職涯方向。", "連結公司發展方向與你想累積的能力。", "low"),
]
_MORE_CHECK = ["準備一段 1 分鐘自我介紹，錄音聽一次", "整理 3 個可量化的成果數字", "查這個職位的市場薪資範圍",
               "準備一個失敗經驗與學到的事", "確認面試時間、地點或視訊連結", "準備要展示的作品或專案連結",
               "複習職缺條件裡你較不熟的那一項"]


async def extend_likely(*, job: dict, resume: dict | None, existing: list[str], note: str, category: str | None,
                        count: int, user_id=None) -> list[dict]:
    def fake() -> LikelyMore:
        items = [Likely(question=q, why=w, tip=t, priority=p) for q, w, t, p in _MORE_LIKELY
                 if not text.is_duplicate(q, existing)]
        if note:
            items.insert(0, Likely(question=f"關於「{note[:20]}」，請分享一個你實際處理過的例子。",
                                   why="你特別想加強這個方向。", tip="用 STAR 結構，最後補上結果與你學到的事。",
                                   priority="medium"))
        return LikelyMore(items=items[:count])
    out = await client.structured(
        purpose="prep_more", schema=LikelyMore, tier="fast", prompt_version=PV_EXTEND, user_id=user_id, fake=fake,
        system="你是求職教練。依職缺與履歷，產生指定數量的「新的」可能面試題目，附為什麼會問與回答建議（繁體中文）。不要和既有題目重複。",
        user=json.dumps({"職缺": job, "履歷": resume or "（沒有履歷）", "既有題目": existing, "使用者需求": note or "（無）",
                         "領域": category or "不限", "數量": count}, ensure_ascii=False))
    res, seen = [], list(existing)
    for it in out.items:
        if text.is_duplicate(it.question, seen):
            continue
        seen.append(it.question)
        res.append({"question": it.question[:300], "why": it.why[:200], "tip": it.tip[:300],
                    "priority": it.priority if it.priority in ("high", "medium", "low") else "medium"})
    return res[:count]


async def extend_checklist(*, job: dict, resume: dict | None, existing: list[str], note: str, count: int,
                           user_id=None) -> list[str]:
    def fake() -> ChecklistMore:
        items = ([f"準備「{note[:20]}」相關的說法"] if note else []) + [c for c in _MORE_CHECK if not text.is_duplicate(c, existing)]
        return ChecklistMore(items=items[:count])
    out = await client.structured(
        purpose="prep_more", schema=ChecklistMore, tier="fast", prompt_version=PV_EXTEND, user_id=user_id, fake=fake,
        system="你是求職教練。依職缺與履歷，產生指定數量的「新的」面試前準備事項（繁體中文、每項 ≤ 30 字）。不要和既有項目重複。",
        user=json.dumps({"職缺": job, "履歷": resume or "（沒有履歷）", "既有項目": existing, "使用者需求": note or "（無）",
                         "數量": count}, ensure_ascii=False))
    res, seen = [], list(existing)
    for it in out.items:
        if it.strip() and not text.is_duplicate(it, seen):
            seen.append(it)
            res.append(it.strip()[:200])
    return res[:count]
