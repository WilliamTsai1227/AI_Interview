"""Question Planner、Rubric Builder。"""
import json

from pydantic import BaseModel, Field

from app.ai import client, text

PV_PLAN = "qplan_v2"
PV_RUBRIC = "rubric_v1"


class Criterion(BaseModel):
    key: str = Field(description="structure|depth|fluency|job_fit|confidence")
    desc: str
    weight: float


class Rubric(BaseModel):
    criteria: list[Criterion]
    must_mention: list[str]


class PlannedQuestion(BaseModel):
    category: str = Field(description="自我介紹|技術深度|系統設計|團隊合作|情境題|動機與規劃")
    difficulty: str = Field(description="basic|medium|advanced")
    text: str = Field(description="面試官會說的題目，口語、≤ 120 字")
    competency: str
    expected_points: list[str]
    rubric: Rubric
    source_reason: str = Field(description="為什麼針對這個職缺／履歷出這題，≤ 40 字")


class PlanOut(BaseModel):
    questions: list[PlannedQuestion]


REALISM = """真實面試的樣子（照這個出題，不要像考卷或教科書）：
- 節奏：請對方簡單介紹經歷 → 挑履歷上一個專案深挖（你負責哪塊、為什麼這樣設計、卡在哪、數字怎麼量的）
  → 職缺核心技能的實務經驗與基本功 → 行為題（意見不合、失敗、壓力、排優先順序）→ 動機與離職原因。
- 看職缺層級與公司類型調整：初階重基礎與學習；資深重深度、取捨、帶人與跨部門影響力；
  外商常問行為題、新創看自主與速度、大型企業看流程與穩定度（從公司簡介判斷，不確定就用一般問法）。
- 問法像人說話：短、直接、一次只問一件事，可以接在對方的經歷後面問（「你在 X 專案提到…，那時候…？」）。
- 不要做這些（真實面試很少這樣問）：
  - 把職缺條文原封不動念出來（「職缺要求『…』，請舉例」）、在題目裡叫對方「用 STAR 回答」。
  - 天馬行空的假設情境（「如果加入第一個月就遇到…」）、抽象題（「用三個關鍵字形容自己」「你是什麼動物」）。
  - 背定義的考古題、腦筋急轉彎、要白板寫長程式碼（使用者要求時例外）。
  - 假設對方做過履歷上沒寫的事。"""

PLAN_SYSTEM = """你是在台灣業界面試過上百人的用人主管，替求職者準備「這個職缺真的會被問到」的模擬面試題。
""" + REALISM + """
出題規則：
- 有履歷時，至少一半的題目從履歷上「真的寫了」的專案、技術、數字切入問細節；沒有履歷時依職缺最核心的工作內容出題。
- 技術題問「實務上怎麼做、為什麼這樣做、遇過什麼問題」；系統設計題只在職缺層級需要時出，題目要貼近這家公司的產品或業務。
- source_reason 寫這題在真實面試中的來由，例如「幾乎每場都會問」「職缺最核心的技能」「履歷上的 X 專案」，≤ 40 字。
- 題目 ≤ 120 字；使用指定語言（zh 繁體中文、en 英文）。不要和「既有題目」重複或只換句話說。
- rubric.criteria 用 structure、depth、fluency、job_fit、confidence 五個 key，權重合計 1。"""


def default_followup(category: str) -> tuple[str, int]:
    return ("none", 1) if category in ("自我介紹", "動機與規劃") else ("adaptive", 1)


def _default_rubric(points: list[str]) -> dict:
    return {"version": "r1", "criteria": [
        {"key": "structure", "desc": "回答是否有清楚的開頭、做法、結果", "weight": 0.25},
        {"key": "depth", "desc": "是否有具體細節、數據與取捨", "weight": 0.25},
        {"key": "fluency", "desc": "表達是否流暢、少贅詞", "weight": 0.15},
        {"key": "job_fit", "desc": "是否連結到職缺需求", "weight": 0.2},
        {"key": "confidence", "desc": "用詞是否肯定", "weight": 0.15}], "must_mention": points[:3]}


def _fake_plan(job: dict, resume: dict | None, categories: list[str], difficulty: str, count: int,
               note: str, existing: list[str]) -> PlanOut:
    co, title = job.get("company_name") or "這間公司", job.get("title") or "這個職位"
    tag = (job.get("tags") or [None])[0] or title
    req = (job.get("requirements") or [None])[0] or f"{title}的核心能力"
    duty = (job.get("duties") or [None])[0] or f"{title}的日常工作"
    skill = ((resume or {}).get("skills") or [None])[0] or tag
    bank = {
        "自我介紹": [("basic", f"請用兩分鐘介紹你自己，以及為什麼想加入 {co}？"),
                  ("basic", f"你目前的工作主要負責什麼？和 {title} 的工作有哪些相似的地方？")],
        "技術深度": [("medium", f"請分享一個和「{tag}」相關、你最有成就感的專案，你負責哪一部分？"),
                  ("advanced", f"你實際做過哪些和「{req}」有關的事？當時最難的地方是什麼？"),
                  ("advanced", f"你在使用 {skill} 時踩過最大的坑是什麼？之後怎麼避免？")],
        "系統設計": [("advanced", f"如果要從零設計「{duty}」相關的系統，你會怎麼規劃架構與取捨？"),
                  ("advanced", "如果流量突然成長十倍，你會先檢查哪些地方？怎麼調整？")],
        "團隊合作": [("medium", "分享一次你和同事意見不同的經驗，最後怎麼取得共識？"),
                  ("medium", "你會怎麼幫助一位剛加入的初階同事快速上手？"),
                  ("basic", "你怎麼讓不熟悉技術的同事理解你的工作內容？")],
        "情境題": [("advanced", "說一次你處理線上事故的經驗：怎麼發現、怎麼止血、事後改了什麼？"),
                ("medium", "如果主管給你一個時程很趕的需求，你會怎麼溝通範圍與優先順序？"),
                ("advanced", "請說明一次你做技術取捨的經驗：有哪些選項、你怎麼決定、結果如何？")],
        "動機與規劃": [("basic", "未來三年，你希望自己在這個職位上有什麼樣的成長？"),
                  ("basic", f"你為什麼想離開目前的工作，選擇 {co}？")],
    }
    order = categories or text.CATEGORIES
    out, used = [], list(existing)
    i = 0
    while len(out) < count and i < 40:
        cat = order[i % len(order)]
        cands = bank.get(cat, bank["情境題"])
        diff, q = cands[(i // len(order)) % len(cands)]
        i += 1
        if note and i == 1:
            q = f"你提到想加強「{note[:20]}」：請分享一個相關的實際經驗，你怎麼做、結果如何？"
        if text.is_duplicate(q, used):
            continue
        used.append(q)
        pts = ["具體情境", "你的角色與做法", "可量化的結果"]
        out.append(PlannedQuestion(category=cat, difficulty=difficulty if difficulty in text.DIFFICULTIES else diff,
                                   text=q, competency=cat, expected_points=pts,
                                   rubric=Rubric(**{k: v for k, v in _default_rubric(pts).items() if k != "version"}),
                                   source_reason=f"依職缺「{title}」{('與你的履歷' if resume else '')}出題"))
    return PlanOut(questions=out)


async def plan_questions(*, job: dict, resume: dict | None, categories: list[str], difficulty: str, count: int,
                         note: str, existing: list[str], language: str = "zh", user_id=None) -> list[dict]:
    user = json.dumps({
        "職缺": {k: job.get(k) for k in ("company_name", "title", "about", "summary", "duties", "requirements", "tags", "description")},
        "履歷": resume or "（沒有履歷，只依職缺出題）",
        "題目類型": categories or "由你依職缺決定，盡量涵蓋不同類型",
        "難度": difficulty, "題數": count, "使用者需求": note or "（無）", "語言": language,
        "既有題目": existing[:60],
    }, ensure_ascii=False)
    out = await client.structured(
        purpose="question_gen", schema=PlanOut, system=PLAN_SYSTEM, user=user, tier="reasoning",
        prompt_version=PV_PLAN, user_id=user_id,
        fake=lambda: _fake_plan(job, resume, categories, difficulty, count, note, existing))
    result, seen = [], list(existing)
    for q in out.questions:
        t = q.text.strip()[:500]
        if not t or text.is_duplicate(t, seen):
            continue
        seen.append(t)
        cat = q.category if q.category in text.CATEGORIES else "情境題"
        mode, n = default_followup(cat)
        rub = q.rubric.model_dump()
        rub["version"] = "r1"
        result.append({"category": cat, "difficulty": q.difficulty if q.difficulty in text.DIFFICULTIES else "medium",
                       "text": t, "competency": q.competency[:60], "expected_points": q.expected_points[:6],
                       "rubric": rub, "source_reason": q.source_reason[:120], "followup_mode": mode,
                       "followup_count": n})
        if len(result) >= count:
            break
    return result


class RubricOut(BaseModel):
    competency: str
    expected_points: list[str]
    rubric: Rubric


async def build_rubric(question: str, job: dict | None, *, user_id=None) -> dict:
    def fake() -> RubricOut:
        pts = ["具體情境", "你的做法", "結果與反思"]
        r = _default_rubric(pts)
        return RubricOut(competency="綜合表達", expected_points=pts,
                         rubric=Rubric(criteria=r["criteria"], must_mention=r["must_mention"]))
    out = await client.structured(
        purpose="rubric", schema=RubricOut, tier="fast", prompt_version=PV_RUBRIC, user_id=user_id, fake=fake,
        system="你是資深面試官。為這道面試題寫出考察能力、預期要點與評分規準（五個 key：structure、depth、fluency、job_fit、confidence，權重合計 1）。",
        user=json.dumps({"題目": question, "職缺": job or {}}, ensure_ascii=False))
    rub = out.rubric.model_dump()
    rub["version"] = "r1"
    return {"competency": out.competency[:60], "expected_points": out.expected_points[:6], "rubric": rub}
