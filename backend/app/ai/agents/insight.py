"""Job Insight：把職缺原文翻成白話，讓使用者看懂公司實際要找什麼人、要拿哪些經驗證明，好準備故事。

跟職缺走、不看履歷（同一個平台職缺所有人共用）；和履歷的比對在面試建議（prep.py）。
"""
import json

from pydantic import BaseModel, Field

from app.ai import client

PV_INSIGHT = "job_insight_v1"


class Focus(BaseModel):
    title: str = Field(description="這個重點在做什麼，白話，≤ 16 字，例如「打造內部 AI 開發平台」")
    quote: str = Field(description="對應的職缺原文片段，保留原文語言，≤ 90 字元")
    meaning: str = Field(description="白話解讀：實際上要做什麼、為什麼公司需要，≤ 110 字")
    evidence: list[str] = Field(description="面試官想聽到的實際經驗 2–3 點，每點 ≤ 40 字")
    story: str = Field(description="建議準備的一個故事，用「講一次你…」開頭，≤ 50 字")


class Experience(BaseModel):
    text: str = Field(description="需要的經驗，≤ 36 字")
    kind: str = Field(description="must|plus")
    proof: str = Field(description="怎麼證明（工作、side project、開源都算），≤ 40 字")


class InsightOut(BaseModel):
    essence: str = Field(description="這個職缺到底在做什麼，≤ 150 字")
    persona: list[str] = Field(description="他們想找的人 3–4 點，每點 ≤ 30 字")
    focus: list[Focus]
    experiences: list[Experience]
    signals: list[str] = Field(description="字裡行間的訊號 2–3 點，每點 ≤ 45 字")


SYSTEM = """你是在台灣科技業當過用人主管、也幫很多人準備過面試的職涯教練。
使用者貼上一個職缺，你要把它「翻成白話」，讓求職者讀完就知道：這個職位實際在做什麼、公司想找什麼樣的人、
要拿哪些實際經驗（工作或實戰專案都算）來證明自己，好開始準備面試故事。繁體中文、口語、具體。

原則：
- 讀懂原文再解讀，不要只是翻譯或換句話說。要點出「這條其實代表什麼」，例如
  「Build and operate the AI infrastructure — agents, evaluation harnesses, telemetry」
  → 他們要的不是只會呼叫 API 的人，而是能把 agent、評測機制、監控做成給整個工程部門用的內部平台，並且自己維運。
- 只根據職缺內容推論；推論要合理、有原文依據，不要編造公司內部資訊、技術棧或團隊規模。
  推測的部分用「看起來」「很可能」帶過。
- 用台灣工程師習慣的說法，技術名詞保留英文（agent、evaluation、telemetry、RAG）。

欄位：
- essence：一段話說清楚這個職缺的本質——在做什麼、服務誰（對外產品還是對內工程師）、團隊大概在什麼階段（從零建立／擴大規模／維運優化）、
  為什麼現在要找這個人。不要重複公司簡介。
- persona：他們心中理想人選的樣子，3–4 點，寫成人的特質加上做過的事（例如「真的在日常開發中重度使用 AI coding agent，知道它哪裡會出錯」），
  不要寫「積極主動」「溝通良好」這種每個職缺都有的話，除非原文特別強調且有具體情境。
- focus：這份工作最核心的 3–5 個重點，依重要性排序。把相關的工作內容與條件合併成一個重點，不要一條原文對一個重點。
  quote 摘原文最關鍵的一句；meaning 白話解讀；evidence 寫面試官會想聽到你「做過」的具體事情（例如「自己設計過 agent 的評測集，
  知道怎麼判斷輸出變好或變差」），不要寫成技能名詞清單；story 是一個能拿來回答的故事題目。
- experiences：4–7 項需要的經驗，先 must（職缺明確要求或工作必須）再 plus（加分）。proof 說明沒有正職經驗時可以怎麼補
  （side project、開源貢獻、工作中的小型導入都算），讓轉職或資歷較淺的人也知道怎麼準備。
- signals：字裡行間透露的訊息，2–3 點，例如某條件出現在文化段落代表什麼、要求「可以帶我們看的專案」代表面試會深挖實作、
  用指標衡量導入成效代表要能說服其他團隊。沒有明顯訊號時給 1 點即可。"""


def _fake(job: dict) -> InsightOut:
    duties = job.get("duties") or [job.get("title") or "職缺的核心工作"]
    reqs = job.get("requirements") or []
    co = job.get("company_name") or "這間公司"
    focus = [Focus(title=d[:16], quote=d[:90], meaning=f"這是 {co} 在這個職位最在意的工作之一：{d}",
                   evidence=[f"實際做過「{d[:20]}」相關的事，並說得出結果"], story=f"講一次你做「{d[:20]}」的經驗")
             for d in duties[:3]]
    exps = [Experience(text=r[:36], kind="must", proof="用工作經驗或 side project 說明你怎麼做到") for r in reqs[:4]]
    return InsightOut(
        essence=job.get("summary") or job.get("about") or f"{co} 在找 {job.get('title') or '這個職位'}。",
        persona=[f"做過「{duties[0][:20]}」相關工作的人"], focus=focus, experiences=exps,
        signals=["（開發模式）設定 OpenAI 金鑰後會有完整解讀"])


async def analyze(job: dict, *, user_id=None) -> InsightOut:
    user = json.dumps({"職缺": {k: job.get(k) for k in ("company_name", "title", "seniority", "about", "summary",
                                                        "duties", "requirements", "tags")},
                       "職缺原文": (job.get("raw_text") or "")[:14000]}, ensure_ascii=False)
    out = await client.structured(purpose="job_insight", schema=InsightOut, system=SYSTEM, user=user,
                                  fake=lambda: _fake(job), tier="reasoning", prompt_version=PV_INSIGHT, user_id=user_id)
    for e in out.experiences:
        if e.kind not in ("must", "plus"):
            e.kind = "plus"
    out.experiences.sort(key=lambda e: e.kind != "must")
    return out
