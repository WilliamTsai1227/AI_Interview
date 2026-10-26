"""Career Planner：依個人檔案＋履歷，(1) 上網搜尋符合意向的職缺 (2) 比對出能力差距並排出準備計畫。"""
import json

from pydantic import BaseModel, Field

from app.ai import client

PV_SEARCH = "career_search_v1"
PV_PLAN = "career_plan_v1"


# ---------- 搜尋職缺 ----------
class FoundJob(BaseModel):
    company_name: str
    title: str
    city: str = Field(description="縣市，例如「台北市」；不知道就空字串")
    salary_text: str = Field(description="原文薪資，例如「月薪 70,000–90,000」；沒有就空字串")
    work_mode: str = Field(description="onsite|hybrid|remote；不知道就空字串")
    source_site: str = Field(description="來源網站，例如 104、cake、linkedin、yourator、company")
    source_url: str = Field(description="該職缺的原始網址，必須是搜尋結果中真實出現的網址")
    summary: str = Field(description="2–3 句摘要，用自己的話改寫，不要貼原文")
    duties: list[str]
    requirements: list[str]
    tags: list[str]


class SearchOut(BaseModel):
    jobs: list[FoundJob]


SEARCH_SYSTEM = """你是求職顧問，使用網路搜尋找出真實、目前仍在招募的職缺。
- 依使用者的求職意向（薪水、產業、職務、工時、地點、工作型態）與履歷背景搜尋，優先台灣的職缺網站（104、CakeResume、Yourator、LinkedIn、公司官網）。
- 只回傳搜尋結果中真的看到的職缺；source_url 必須是真實網址，不確定就不要列，寧缺勿濫。
- 摘要與條列用自己的話改寫，不要整段貼原文。最多 {n} 筆，繁體中文。"""


def profile_text(profile: dict) -> str:
    sal = profile.get("salary_min")
    per = "月薪" if profile.get("salary_period") == "month" else "年薪"
    lines = [f"期望薪資：{per} {sal:,} 以上" if sal else "期望薪資：不限",
             f"產業：{'、'.join(profile.get('industries') or []) or '不限'}",
             f"想做的職務：{'、'.join(profile.get('roles') or []) or '不限'}",
             f"希望的工作內容：{profile.get('duties_pref') or '未填'}",
             f"工時偏好：{profile.get('work_hours') or '未填'}",
             f"地點：{'、'.join(profile.get('locations') or []) or '不限'}",
             f"工作型態：{'、'.join(profile.get('work_modes') or []) or '不限'}",
             f"自我介紹：{profile.get('self_intro') or '未填'}",
             f"經歷補充：{profile.get('experience_note') or '未填'}"]
    return "\n".join(lines)


async def search_jobs(*, profile: dict, resume: dict | None, count: int = 6, user_id=None) -> list[FoundJob]:
    """開發模式（沒有金鑰）無法上網，回傳空清單，只用平台職缺。"""
    user = f"【求職意向】\n{profile_text(profile)}\n\n【履歷】\n{json.dumps(resume, ensure_ascii=False)[:6000] if resume else '尚未上傳'}"
    out = await client.structured(
        purpose="career_search", schema=SearchOut, system=SEARCH_SYSTEM.format(n=count), user=user,
        fake=lambda: SearchOut(jobs=[]), tier="fast", prompt_version=PV_SEARCH, user_id=user_id,
        timeout=120, attempts=1, web_search=True)
    return out.jobs[:count]


# ---------- 差距分析＋準備計畫 ----------
class Gap(BaseModel):
    skill: str = Field(description="缺口名稱，≤ 15 字")
    severity: str = Field(description="high|medium|low")
    detail: str = Field(description="為什麼是缺口、多少職缺要求，≤ 60 字")


class JobFit(BaseModel):
    index: int = Field(description="職缺編號（輸入清單中的 #）")
    score: int = Field(description="契合度 0–100")
    reason: str = Field(description="一句話說明，≤ 50 字")
    gaps: list[str] = Field(description="這個職缺特有的補強點，0–3 項，每項 ≤ 20 字")


class Area(BaseModel):
    title: str = Field(description="準備大項目，≤ 15 字，例如「系統設計基礎」")
    why: str = Field(description="為什麼要準備，≤ 50 字")
    priority: str = Field(description="high|medium|low")
    tasks: list[str] = Field(description="這個大項目下的小項目 3–6 個，每項是可執行的具體行動，≤ 40 字")


class PlanOut(BaseModel):
    summary: str = Field(description="≤ 80 字的總評")
    readiness_score: int = Field(description="整體準備度 0–100")
    strengths: list[str] = Field(description="2–4 點，每點 ≤ 25 字")
    gaps: list[Gap]
    job_fits: list[JobFit]
    areas: list[Area] = Field(description="準備大項目 3–6 個，依優先順序")


PLAN_SYSTEM = """你是資深職涯教練。根據使用者的履歷、自我介紹與求職意向，比對候選職缺，找出「個人能力還缺什麼」，並排出完整的準備計畫（繁體中文、口語、具體）。
- gaps：跨職缺反覆出現的能力缺口 3–6 個，severity 依出現頻率與重要性。只根據履歷與自我介紹中明確的資訊判斷，不要捏造使用者的經歷。
- job_fits：每個候選職缺一筆，index 對應清單編號。
- areas：3–6 個準備大項目（例如技術深度、作品集、面試表達、產業知識），每個底下 3–6 個可執行的小項目，並有優先順序。
- 若沒有履歷，gaps 與 areas 以職缺共同要求與自我介紹為主，並建議先上傳履歷。"""


def _job_line(i: int, j: dict) -> str:
    return (f"#{i} {j['company_name']}｜{j['title']}｜{j.get('salary_text') or '薪資未提'}｜{j.get('city') or ''}\n"
            f"  要求：{'；'.join(j.get('requirements') or [])[:300]}\n  標籤：{', '.join(j.get('tags') or [])}")


async def build_plan(*, profile: dict, resume: dict | None, jobs: list[dict], user_id=None) -> PlanOut:
    def fake() -> PlanOut:
        skills = {s.lower() for s in (resume or {}).get("skills") or []}
        freq: dict[str, int] = {}
        for j in jobs:
            for t in j.get("tags") or []:
                if t.lower() not in skills:
                    freq[t] = freq.get(t, 0) + 1
        missing = sorted(freq, key=lambda k: -freq[k])[:4] or ["量化成果表達"]
        fits = []
        for i, j in enumerate(jobs):
            miss = [t for t in j.get("tags") or [] if t.lower() not in skills][:2]
            hit = len(j.get("tags") or []) - len(miss)
            fits.append(JobFit(index=i, score=min(95, 55 + 8 * hit + (10 if resume else 0)),
                               reason="技能與職缺條件部分相符" if hit else "需要補強職缺要求的核心技能", gaps=miss))
        return PlanOut(
            summary="先補上多個職缺共同要求的技能，再把履歷成果量化。" if resume else "先上傳履歷，才能精準比對；以下先依職缺共同要求規劃。",
            readiness_score=65 if resume else 40,
            strengths=(["履歷技能與部分職缺相符"] if resume and skills else []),
            gaps=[Gap(skill=m, severity="high" if k == 0 else "medium", detail=f"{freq.get(m, 1)} 個候選職缺有提到") for k, m in enumerate(missing)],
            job_fits=fits,
            areas=[Area(title=f"補強：{m}", why="多個目標職缺都有要求。", priority="high" if k == 0 else "medium",
                        tasks=[f"整理 {m} 的基本觀念與常見面試題", f"做一個用到 {m} 的小專案或練習", f"把 {m} 的成果寫進履歷"]) for k, m in enumerate(missing[:3])]
            + [Area(title="履歷與面試表達", why="讓你的經驗被看見。", priority="medium",
                    tasks=["每段經歷補上角色與可量化的成果", "準備 2 分鐘自我介紹", "用 STAR 結構準備 3 個故事"])])

    jobs_txt = "\n".join(_job_line(i, j) for i, j in enumerate(jobs)) or "（沒有找到候選職缺，請依求職意向規劃）"
    user = (f"【求職意向與自我介紹】\n{profile_text(profile)}\n\n【履歷】\n"
            f"{json.dumps(resume, ensure_ascii=False)[:8000] if resume else '尚未上傳'}\n\n【候選職缺】\n{jobs_txt}")
    return await client.structured(purpose="career_plan", schema=PlanOut, system=PLAN_SYSTEM, user=user, fake=fake,
                                   tier="reasoning", prompt_version=PV_PLAN, user_id=user_id)
