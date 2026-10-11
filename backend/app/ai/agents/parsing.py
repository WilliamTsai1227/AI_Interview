"""Resume Parser、JD Parser（architecture.md §7）。"""
import re

from pydantic import BaseModel, Field

from app.ai import client, text

PV_RESUME = "resume_v1"
PV_JD = "jd_v1"


class Experience(BaseModel):
    org: str
    title: str
    start: str = Field(description="YYYY-MM，不知道就空字串")
    end: str = Field(description="YYYY-MM；仍在職為空字串")
    bullets: list[str]


class Project(BaseModel):
    name: str
    role: str
    impact: str


class ResumeOut(BaseModel):
    name: str
    summary: str
    years_experience: float
    experiences: list[Experience]
    education: list[str]
    skills: list[str]
    projects: list[Project]
    achievements: list[str]
    highlights: list[str] = Field(description="履歷亮點，2–3 點，每點 ≤ 20 字")
    improvements: list[str] = Field(description="可以更好，2–3 點，每點 ≤ 20 字")


RESUME_SYSTEM = """你是履歷解析助手。從使用者的履歷文字擷取結構化資料，只使用履歷中明確出現的資訊，不要捏造。
highlights 與 improvements 用繁體中文、口語、具體，對求職者有幫助。"""


async def parse_resume(raw: str, *, user_id=None) -> ResumeOut:
    def fake() -> ResumeOut:
        skills = text.find_tags(raw, 10)
        nums = re.findall(r"\d+(?:\.\d+)?\s*%", raw)
        first = next((l.strip() for l in raw.splitlines() if l.strip()), "")
        return ResumeOut(
            name=first[:20], summary=re.sub(r"\s+", " ", raw)[:160], years_experience=0, experiences=[],
            education=[], skills=skills, projects=[], achievements=[f"成果提升 {n}" for n in nums[:3]],
            highlights=(["成果有量化數字"] if nums else []) + (["技能清楚列出"] if skills else []) or ["履歷內容完整"],
            improvements=["每段經歷補上你的角色與成果", "加上可量化的數字"] if not nums else ["補上帶人或跨部門合作經驗"])
    return await client.structured(purpose="resume_parse", schema=ResumeOut, system=RESUME_SYSTEM,
                                   user=raw[:24000], fake=fake, tier="fast", prompt_version=PV_RESUME,
                                   user_id=user_id)


class JDOut(BaseModel):
    is_job_post: bool = Field(description="內容看起來是不是職缺（至少有職稱與工作內容）")
    company_name: str
    title: str
    city: str = Field(description="台北市等縣市；不知道為空字串")
    location_text: str
    employment_type: str
    work_mode: str = Field(description="onsite|hybrid|remote；不知道為空字串")
    salary_text: str
    about: str = Field(description="公司與職位簡介，≤ 120 字")
    duties: list[str]
    requirements: list[str]
    tags: list[str] = Field(description="技能或領域關鍵字，≤ 6 個")
    work_language: str = Field(description="zh 或 en")
    seniority: str = Field(description="junior|mid|senior|lead")
    category_code: str = Field(description="frontend|backend|fullstack|mobile|data|ml|devops|qa|pm|design|other")


JD_SYSTEM = """你是職缺解析助手。把使用者貼上的職缺內容整理成結構化欄位。只使用原文中的資訊；沒有的欄位給空字串或空陣列。
duties 與 requirements 每條 ≤ 40 字，保留原意。"""


def rough_jd(raw: str) -> JDOut:
    """沒有 AI 或 AI 失敗時：用規則粗解析職缺，讓後續出題仍可進行。"""
    return _fake_jd(raw)


async def parse_jd(raw: str, *, user_id=None) -> JDOut:
    return await client.structured(purpose="jd_parse", schema=JDOut, system=JD_SYSTEM, user=raw[:16000],
                                   fake=lambda: _fake_jd(raw), tier="fast", prompt_version=PV_JD, user_id=user_id)


def _fake_jd(raw: str) -> JDOut:
    p = text.rough_parse_jd(raw)
    low = raw.lower()
    mode = "remote" if ("遠端" in raw or "remote" in low) else "hybrid" if ("混合" in raw or "hybrid" in low) else ""
    seniority = "senior" if re.search(r"senior|資深|lead|主管", low) else "junior" if re.search(r"junior|初階|新鮮人", low) else "mid"
    cat = "frontend" if re.search(r"front|前端|react|vue", low) else "backend" if re.search(r"back|後端|python|java|go\b", low) else "other"
    return JDOut(is_job_post=len(raw.strip()) >= 20, company_name=p["company_name"], title=p["title"],
                 city=p["city"] or "", location_text=p["location_text"] or "", employment_type="",
                 work_mode=mode, salary_text="", about=p["about"] or "", duties=p["duties"],
                 requirements=p["requirements"], tags=p["tags"],
                 work_language="en" if sum(c.isascii() for c in raw) > len(raw) * 0.8 else "zh",
                 seniority=seniority, category_code=cat)
