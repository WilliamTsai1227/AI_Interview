"""Resume Reviser：依履歷＋（選填的）目標職缺產生挖掘卡片，再依使用者的回答產生新版履歷（逐條標出改動）。"""
import json

from pydantic import BaseModel, Field

from app.ai import client

PV_CARDS = "revision_cards_v1"
PV_RESUME = "revision_resume_v1"

GOALS = {"target": "投特定職缺", "refresh": "整體更新履歷", "switch": "轉職到新的領域", "first": "找第一份工作"}
LANGS = {"zh": "繁體中文", "en": "英文"}
LENGTHS = {"one": "一頁", "two": "兩頁"}


def _setup_text(setup: dict) -> str:
    return (f"目的：{GOALS.get(setup.get('goal'), '整體更新履歷')}\n輸出語言：{LANGS.get(setup.get('language'), '繁體中文')}\n"
            f"篇幅：{LENGTHS.get(setup.get('length'), '一頁')}\n使用者對履歷解析結果的修正：{setup.get('correction') or '沒有，解析結果正確'}")


def _jobs_text(jobs: list[dict]) -> str:
    return "\n".join(f"#{i} {j['company_name']}｜{j['title']}\n  工作內容：{'；'.join(j.get('duties') or [])[:400]}\n"
                     f"  要求：{'；'.join(j.get('requirements') or [])[:400]}\n  標籤：{', '.join(j.get('tags') or [])}"
                     for i, j in enumerate(jobs)) or "（使用者沒有選職缺：做整體改善，不要依任何職缺的要求去問或去改）"


def _resume_text(parsed: dict | None, raw: str | None) -> str:
    return (f"【結構化】\n{json.dumps(parsed or {}, ensure_ascii=False)[:8000]}\n\n"
            f"【原文】\n{(raw or '')[:12000] or '（沒有）'}")


# ---------- 挖掘卡片 ----------
class CardOption(BaseModel):
    label: str = Field(description="選項文字，≤ 10 字，例如「工作上用過」「沒有」")
    opens_detail: bool = Field(description="選了這個選項要不要展開輸入框請使用者補細節（代表「有」的選項才展開）")


class Card(BaseModel):
    kind: str = Field(description="gap（職缺要、履歷沒寫）|quantify（有寫但缺數字）|missing（可能漏寫的經歷）|clarify（角色或範圍不清楚）")
    context: str = Field(description="為什麼問這題，≤ 40 字，例如「職缺要求 Kubernetes，你的履歷沒提到」")
    question: str = Field(description="問使用者的一句話，口語，≤ 50 字")
    options: list[CardOption] = Field(description="2–4 個選項；missing 卡可以給空清單（直接打字回答）")
    detail_prompt: str = Field(description="展開輸入框時問的追問，≤ 40 字，例如「在哪個專案、用來做什麼？」")
    example: str = Field(description="範例回答，讓使用者知道要寫到多具體，≤ 50 字")


class CardsOut(BaseModel):
    cards: list[Card] = Field(description="6–10 張，由簡單到需要描述的排序")


CARDS_SYSTEM = """你是資深履歷顧問，要幫使用者把「做過、但履歷沒寫好」的東西問出來（繁體中文、口語）。
你會拿到使用者的履歷、這次修改的設定，以及（選填的）想投的職缺。產生 6–10 張問題卡片，像語言學習 App 開始前的卡片，一張只問一件事、大多點一下就能回答。
- 有職缺時：gap 卡問「職缺反覆要求、但履歷完全沒提到」的能力（3–5 張）。履歷已經寫了的不要問。選項例如「工作上用過」「個人專案用過」「學過沒實戰」「沒有」，前兩個 opens_detail=true。
- 沒有職缺時：不要出 gap 卡，也不要假設任何職缺的要求；改依「目的」問（例如轉職就問可以轉移的經驗）。
- quantify 卡：挑履歷裡最重要、但沒有數字的 2–3 條成果，問規模或改善幅度。選項給合理的範圍（例如「不到 2 倍」「2–5 倍」「5 倍以上」「不確定」），代表有數字的 opens_detail=true，讓使用者填實際數字。
- clarify 卡：角色或範圍寫得模糊的地方（例如「參與」「負責」），問是自己做、主導還是協助。
- missing 卡：最後放 1–2 張開放題，問可能漏寫的專案、獲獎、社群、證照，options 給空清單。
- context 要具體指出依據（哪個職缺要求、履歷哪一條），不要寫內部編號 #0。
- 不要問個資（電話、地址）、薪資、年齡等與履歷內容無關的問題。"""


async def build_cards(*, setup: dict, parsed: dict | None, raw: str | None, jobs: list[dict], user_id=None) -> CardsOut:
    def fake() -> CardsOut:
        have = {s.lower() for s in (parsed or {}).get("skills") or []}
        missing = [t for j in jobs for t in j.get("tags") or [] if t.lower() not in have]
        cards = [Card(kind="gap", context=f"職缺要求 {t}，你的履歷沒提到", question=f"你用過 {t} 嗎？",
                      options=[CardOption(label="工作上用過", opens_detail=True), CardOption(label="個人專案用過", opens_detail=True),
                               CardOption(label="學過沒實戰", opens_detail=False), CardOption(label="沒有", opens_detail=False)],
                      detail_prompt="在哪個專案、用來做什麼？", example=f"在訂單系統用 {t} 部署 12 個服務")
                 for t in list(dict.fromkeys(missing))[:3]]
        cards.append(Card(kind="quantify", context="你的經歷寫了成果，但沒有數字", question="你做過最有成就感的改善，大概改善了多少？",
                          options=[CardOption(label="不到 2 倍", opens_detail=True), CardOption(label="2–5 倍", opens_detail=True),
                                   CardOption(label="5 倍以上", opens_detail=True), CardOption(label="不確定", opens_detail=False)],
                          detail_prompt="實際的數字是多少？", example="API 回應時間從 800ms 降到 200ms"))
        cards.append(Card(kind="clarify", context="履歷寫「參與」，看不出你的角色", question="這些專案大多是你主導，還是協助？",
                          options=[CardOption(label="自己主導", opens_detail=True), CardOption(label="跟團隊一起", opens_detail=True),
                                   CardOption(label="協助別人", opens_detail=False)],
                          detail_prompt="你具體負責哪一塊？", example="我負責設計資料表和 API，帶 2 位新人"))
        cards.append(Card(kind="missing", context="很多人會漏寫自己的 side project", question="最近一年有沒有沒寫進履歷的專案或成就？",
                          options=[], detail_prompt="說說看是什麼、你做了什麼", example="用 FastAPI 做了一個 AI 模擬面試網站，有 50 位使用者"))
        return CardsOut(cards=cards)

    user = f"【設定】\n{_setup_text(setup)}\n\n【履歷】\n{_resume_text(parsed, raw)}\n\n【想投的職缺】\n{_jobs_text(jobs)}"
    return await client.structured(purpose="revision_cards", schema=CardsOut, system=CARDS_SYSTEM, user=user, fake=fake,
                                   tier="fast", prompt_version=PV_CARDS, user_id=user_id)


def answers_text(cards: list[dict], answers: dict) -> str:
    out = []
    for i, c in enumerate(cards):
        a = answers.get(str(i)) or {}
        if a.get("skipped") or not (a.get("choice") or a.get("text")):
            reply = "（跳過，沒有回答：不能當作有或沒有）"
        else:
            reply = "；".join(x for x in (a.get("choice"), (a.get("text") or "").strip()) if x)
        out.append(f"卡片 {i}（{c['kind']}）{c['context']}\n  問：{c['question']}\n  答：{reply}")
    return "\n".join(out) or "（沒有卡片）"


# ---------- 新版履歷 ----------
class Line(BaseModel):
    text: str = Field(description="這一條的內容（條列項目或一段文字）")
    change: str = Field(description="keep（原文照用）|edit（改寫原有的一條）|new（依使用者回答新增）")
    original: str = Field(description="edit 時的原文；keep、new 給空字串")
    reason: str = Field(description="edit、new 的理由，≤ 40 字，例如「補上你回答的數字」「對應職缺的 CI/CD 要求」；keep 給空字串")
    card: int = Field(description="new、edit 依據的卡片序號；跟卡片無關給 -1")


class Entry(BaseModel):
    title: str = Field(description="職稱｜公司、專案名稱、學校等；摘要、技能這種沒有標題的區塊給空字串")
    meta: str = Field(description="期間、地點等補充，例如「2022/03 – 至今・台北」；沒有就給空字串")
    lines: list[Line]


class Section(BaseModel):
    title: str = Field(description="區塊標題，例如「摘要」「工作經歷」「專案」「技能」「學歷」")
    entries: list[Entry]


class RevisionOut(BaseModel):
    name: str
    headline: str = Field(description="名字下面的一行定位，例如「後端工程師・4 年經驗」")
    contact: str = Field(description="原履歷裡的聯絡方式（Email、電話、連結）整理成一行；原履歷沒有就給空字串")
    sections: list[Section]
    summary: str = Field(description="這次改了什麼，給使用者看的一段話，≤ 80 字")
    tips: list[str] = Field(description="還可以再補強的地方 0–3 點，每點 ≤ 40 字（例如還有待補的數字、選了「沒有」的職缺要求）")


RESUME_SYSTEM = """你是資深履歷顧問，要依使用者的原履歷、這次的設定、（選填的）想投的職缺，以及使用者對問題卡片的回答，產生一份新版履歷。
最重要的規則：不捏造。
- 新增的內容（change=new）只能來自使用者在卡片上的回答，card 填那張卡的序號。回答「沒有」「學過沒實戰」或跳過的，不能寫成會、做過。
- 使用者沒給的數字、規模不要自己編；要寫的話用中括號留待補標記，例如「讓查詢速度提升 [多少倍？]」。
- 改寫（change=edit）只改表達：動詞開頭、寫出角色與成果、補上使用者回答的數字、用職缺的用語；original 放原文。意思不能比原文更誇大。
- 原文寫得已經很好的就 keep，原文照抄，不要為了改而改。
- 有職缺時：把跟職缺相關的經歷、技能往前排，用詞對齊職缺；沒有職缺時做整體改善，不要依任何職缺的要求去改。
- 依設定的輸出語言寫（英文時，original 仍放原文）；篇幅一頁時要精簡，次要的經歷可以縮成一兩條。
- 保留原履歷的重要資訊：學歷、工作期間、公司名稱、聯絡方式都不能弄丟或改錯。
- 區塊順序：摘要 → 工作經歷 → 專案 → 技能 → 學歷（可以依目的調整，例如第一份工作把專案、學歷往前）。
- reason、summary、tips 用繁體中文寫給使用者看；提到卡片時說「你在第 N 題的回答」，N 是卡片序號 + 1。"""


async def build_resume(*, setup: dict, parsed: dict | None, raw: str | None, jobs: list[dict], cards: list[dict], answers: dict,
                       user_id=None) -> RevisionOut:
    def fake() -> RevisionOut:
        p = parsed or {}
        exp = [Entry(title=f"{e.get('title', '')}｜{e.get('org', '')}", meta=f"{e.get('start', '')} – {e.get('end') or '至今'}",
                     lines=[Line(text=b, change="keep", original="", reason="", card=-1) for b in e.get("bullets") or []])
               for e in p.get("experiences") or []]
        new = [Line(text=f"{c['question'].rstrip('？?')}：{(answers.get(str(i)) or {}).get('text', '').strip()}", change="new", original="",
                    reason=f"來自你在第 {i + 1} 題的回答", card=i)
               for i, c in enumerate(cards) if ((answers.get(str(i)) or {}).get("text") or "").strip()]
        sections = [Section(title="摘要", entries=[Entry(title="", meta="", lines=[
            Line(text=p.get("summary") or "[用一兩句話介紹你自己]", change="keep" if p.get("summary") else "new", original="",
                 reason="" if p.get("summary") else "原履歷沒有摘要", card=-1)])])]
        if exp or new:
            sections.append(Section(title="工作經歷", entries=exp + ([Entry(title="補充經歷", meta="", lines=new)] if new else [])))
        if p.get("skills"):
            sections.append(Section(title="技能", entries=[Entry(title="", meta="", lines=[Line(text="、".join(p["skills"]), change="keep",
                                                                                              original="", reason="", card=-1)])]))
        if p.get("education"):
            sections.append(Section(title="學歷", entries=[Entry(title="", meta="", lines=[
                Line(text=x, change="keep", original="", reason="", card=-1) for x in p["education"]])]))
        return RevisionOut(name=p.get("name") or "", headline=f"{p.get('years_experience') or 0:g} 年經驗", contact="", sections=sections,
                           summary=f"依你的 {len(new)} 個回答補進履歷（開發模式的假結果）。", tips=["補上待補的數字"] if not new else [])

    user = (f"【設定】\n{_setup_text(setup)}\n\n【原履歷】\n{_resume_text(parsed, raw)}\n\n【想投的職缺】\n{_jobs_text(jobs)}\n\n"
            f"【使用者對問題卡片的回答】\n{answers_text(cards, answers)}")
    return await client.structured(purpose="revision_resume", schema=RevisionOut, system=RESUME_SYSTEM, user=user, fake=fake,
                                   tier="reasoning", prompt_version=PV_RESUME, user_id=user_id)
