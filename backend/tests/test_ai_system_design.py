"""AI 系統設計知識表：只有 AI 工程職缺才放進出題、追問、評分；挑出的主題跟職缺內容有關。"""
from app.ai.knowledge import ai_system_design as asd

AI_JOB = {"title": "AI 工程師（LLM 應用）", "requirements": ["熟悉 RAG 與向量資料庫（pgvector、Qdrant）", "有 LLM 評估與監控經驗"],
          "duties": ["設計客服 AI agent 與工具呼叫", "優化推論延遲與成本"], "tags": ["LLM", "RAG", "Python"]}
BACKEND_JOB = {"title": "資深後端工程師", "about": "Acme AI 是電商 SaaS 公司", "requirements": ["3 年 FastAPI 經驗", "熟悉 Redis、Docker"],
               "tags": ["Python", "PostgreSQL"]}


def test_detects_ai_jobs_only():
    assert asd.is_ai_job(AI_JOB)
    assert asd.is_ai_job({"title": "Machine Learning Engineer"})
    assert not asd.is_ai_job(BACKEND_JOB)          # 公司名稱有 AI 不算
    assert not asd.is_ai_job({"title": "前端工程師", "requirements": ["React", "TypeScript"]})
    assert not asd.is_ai_job(None)


def test_picks_topics_from_job_text():
    keys = [t["key"] for t in asd.pick_topics(AI_JOB, 4)]
    assert "rag" in keys and len(keys) == 4
    # 職缺沒寫細節時用最常考的主題補滿
    assert [t["key"] for t in asd.pick_topics({"title": "LLM Engineer"}, 4)] == asd.DEFAULT_KEYS


def test_context_only_for_ai_jobs():
    ref = asd.context(AI_JOB)
    assert ref and len(ref["相關主題"]) == 4 and len(ref["解題步驟"]) == 8
    assert asd.context(BACKEND_JOB) is None
    assert asd.probes(BACKEND_JOB) is None
    assert asd.eval_guide(AI_JOB, "系統設計") and asd.eval_guide(AI_JOB, "團隊合作") is None
