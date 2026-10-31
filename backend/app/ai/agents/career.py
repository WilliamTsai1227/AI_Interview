"""Career Planner：依個人檔案＋履歷，比對使用者自己的目標職缺，找出能力差距並排出準備計畫。"""
import json

from pydantic import BaseModel, Field

from app.ai import client

PV_PLAN = "career_plan_v2"


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
             f"經歷補充：{profile.get('experience_note') or '未填'}",
             f"作品集連結：{'、'.join(profile.get('portfolio_links') or []) or '未填'}"]
    return "\n".join(lines)


# ---------- 差距分析＋準備計畫 ----------
class Gap(BaseModel):
    skill: str = Field(description="缺口名稱，≤ 15 字")
    severity: str = Field(description="high|medium|low")
    detail: str = Field(description="為什麼是缺口、多少職缺要求，≤ 60 字")


class JobFit(BaseModel):
    index: int = Field(description="目標職缺編號（輸入清單中的 #）")
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


PLAN_SYSTEM = """你是資深職涯教練。根據使用者的履歷、自我介紹與求職意向，找出「個人能力還缺什麼」，並排出完整的準備計畫（繁體中文、口語、具體）。
- 【目標職缺】是使用者自己挑選、想應徵的職缺，是判斷缺口最重要的依據。
- 有目標職缺時：gaps 是這些職缺反覆要求、但使用者還不具備的能力 3–6 個，severity 依出現頻率與重要性；job_fits 每個目標職缺一筆，index 對應清單編號。
- 沒有目標職缺時：依使用者想做的職務、產業與一般常見的職務要求判斷 gaps，job_fits 給空清單；不要編造具體公司或職缺。summary 要提醒使用者加入目標職缺後重新分析會更準。
- 只根據履歷與自我介紹中明確的資訊判斷使用者會什麼，不要捏造經歷。
- areas：3–6 個準備大項目（例如技術深度、作品集、面試表達、產業知識），每個底下 3–6 個可執行的小項目，並有優先順序。
- 作品集連結若有提供，把它當作能力證據；沒有作品集或作品集不足時，在 areas 加入「建立作品集」相關小項目。
- 沒有履歷時，建議先上傳履歷。"""


def _job_line(i: int, j: dict) -> str:
    return (f"#{i} {j['company_name']}｜{j['title']}｜{j.get('salary_text') or '薪資未提'}｜{j.get('city') or ''}\n"
            f"  工作內容：{'；'.join(j.get('duties') or [])[:300]}\n"
            f"  要求：{'；'.join(j.get('requirements') or [])[:300]}\n  標籤：{', '.join(j.get('tags') or [])}")


async def build_plan(*, profile: dict, resume: dict | None, jobs: list[dict], user_id=None) -> PlanOut:
    def fake() -> PlanOut:
        skills = {s.lower() for s in (resume or {}).get("skills") or []}
        freq: dict[str, int] = {}
        for j in jobs:
            for t in j.get("tags") or []:
                if t.lower() not in skills:
                    freq[t] = freq.get(t, 0) + 1
        missing = sorted(freq, key=lambda k: -freq[k])[:4] or (profile.get("roles") or [])[:1] or ["量化成果表達"]
        fits = []
        for i, j in enumerate(jobs):
            miss = [t for t in j.get("tags") or [] if t.lower() not in skills][:2]
            hit = len(j.get("tags") or []) - len(miss)
            fits.append(JobFit(index=i, score=min(95, 55 + 8 * hit + (10 if resume else 0)),
                               reason="技能與職缺條件部分相符" if hit else "需要補強職缺要求的核心技能", gaps=miss))
        return PlanOut(
            summary=("先補上目標職缺共同要求的技能，再把履歷成果量化。" if jobs else "還沒有目標職缺，先依你想做的職務規劃；加入目標職缺後重新分析會更準。")
            if resume else "先上傳履歷，才能精準比對。",
            readiness_score=65 if resume else 40,
            strengths=(["履歷技能與部分職缺相符"] if resume and skills else []),
            gaps=[Gap(skill=m, severity="high" if k == 0 else "medium",
                      detail=f"{freq[m]} 個目標職缺有提到" if m in freq else "你想做的職務常見要求") for k, m in enumerate(missing)],
            job_fits=fits,
            areas=[Area(title=f"補強：{m}", why="目標職缺或想做的職務常見要求。", priority="high" if k == 0 else "medium",
                        tasks=[f"整理 {m} 的基本觀念與常見面試題", f"做一個用到 {m} 的小專案或練習", f"把 {m} 的成果寫進履歷"]) for k, m in enumerate(missing[:3])]
            + [Area(title="履歷與面試表達", why="讓你的經驗被看見。", priority="medium",
                    tasks=["每段經歷補上角色與可量化的成果", "準備 2 分鐘自我介紹", "用 STAR 結構準備 3 個故事"])])

    jobs_txt = "\n".join(_job_line(i, j) for i, j in enumerate(jobs)) or "（使用者還沒有目標職缺，請依求職意向與想做的職務規劃）"
    user = (f"【求職意向與自我介紹】\n{profile_text(profile)}\n\n【履歷】\n"
            f"{json.dumps(resume, ensure_ascii=False)[:8000] if resume else '尚未上傳'}\n\n【目標職缺】\n{jobs_txt}")
    return await client.structured(purpose="career_plan", schema=PlanOut, system=PLAN_SYSTEM, user=user, fake=fake,
                                   tier="reasoning", prompt_version=PV_PLAN, user_id=user_id)
