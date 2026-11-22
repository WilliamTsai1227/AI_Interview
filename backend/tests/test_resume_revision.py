"""履歷修改：建立 → 產生卡片 → 回答 → 產生新版履歷 → 逐條決定 → Word → 存成新履歷。

開發模式（假 AI）執行，不會呼叫 OpenAI；背景工作由測試直接執行，不需要 worker。檔案儲存換成記憶體，不需要 MinIO。
需要 docker compose 的 PostgreSQL 與 Redis。執行：.venv/bin/pytest tests/test_resume_revision.py -q
"""
import datetime as dt
import io
import uuid

import pytest

from app.ai.agents import revision as rv
from app.core import db
from app.core.config import settings
from app.modules.revisions import service
from app.workers import main as worker
from tests.conftest import API, register

JD = ("資深後端工程師\n公司：Acme Cloud\n工作內容：\n- 設計並維護 Python API\n- 優化 PostgreSQL 查詢\n"
      "條件要求：\n- 3 年以上 FastAPI 經驗\n- 熟悉 Kubernetes 與 Docker")
PARSED = {"name": "王小明", "summary": "後端工程師 4 年", "years_experience": 4, "education": ["台灣大學 資訊工程學系"],
          "skills": ["Python", "FastAPI"], "projects": [], "achievements": [],
          "experiences": [{"org": "Acme", "title": "後端工程師", "start": "2021-03", "end": "", "bullets": ["負責訂單 API 開發"]}]}


@pytest.fixture()
def dev_ai(monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "")      # 假 AI


@pytest.fixture()
def files(monkeypatch):
    store: dict[str, bytes] = {}

    async def put(key, data, content_type):
        store[key] = data

    async def delete(*keys):
        for k in keys:
            store.pop(k, None)
    monkeypatch.setattr(service.storage, "put", put)
    monkeypatch.setattr(service.storage, "delete", delete)
    return store


def _drain(client, since: dt.datetime, kinds: tuple[str, ...] | None = None, rounds: int = 10) -> None:
    async def run_once() -> int:
        async with db.connection() as conn:
            jobs = await conn.fetch(
                """SELECT id, kind, payload FROM background_jobs WHERE status IN ('queued','retrying') AND created_at >= $1
                      AND ($2::text[] IS NULL OR kind = ANY($2)) ORDER BY id""", since, list(kinds) if kinds else None)
        for j in jobs:
            await worker.HANDLERS[j["kind"]](j["payload"])
            async with db.connection() as conn:
                await conn.execute("UPDATE background_jobs SET status = 'succeeded', finished_at = now() WHERE id = $1", j["id"])
        return len(jobs)
    for _ in range(rounds):
        if not client.portal.call(run_once):
            return


def _resume(client, uid: str, status: str = "parsed") -> str:
    async def ins() -> str:
        async with db.connection() as conn:
            return str(await conn.fetchval(
                """INSERT INTO resumes (user_id, filename, mime_type, size_bytes, file_key, sha256, is_primary, parse_status, parsed_json, parsed_text)
                   VALUES ($1, '履歷.pdf', 'application/pdf', 100, $2, $3,
                           NOT EXISTS (SELECT 1 FROM resumes WHERE user_id = $1 AND is_primary), $4, $5, $6) RETURNING id""",
                uuid.UUID(uid), f"users/{uid}/resumes/x.pdf", uuid.uuid4().hex, status, PARSED if status == "parsed" else None,
                "王小明\nwang@example.com\n後端工程師" if status == "parsed" else None))
    return client.portal.call(ins)


# ---------- 純函式 ----------
def test_final_lines_applies_decisions():
    result = {"name": "小明", "headline": "", "contact": "", "sections": [{"title": "經歷", "entries": [{"title": "Acme", "meta": "", "lines": [
        {"id": "a", "text": "主導訂單 API 重構", "change": "edit", "original": "負責訂單 API", "reason": "", "card": -1},
        {"id": "b", "text": "用 Kubernetes 部署 12 個服務", "change": "new", "original": "", "reason": "", "card": 0},
        {"id": "c", "text": "維護 CI", "change": "keep", "original": "", "reason": "", "card": -1}]}]}]}
    doc = service.final_lines(result, {})
    assert doc["sections"][0]["entries"][0]["lines"] == ["主導訂單 API 重構", "用 Kubernetes 部署 12 個服務", "維護 CI"]
    doc = service.final_lines(result, {"a": {"accept": False}, "b": {"accept": False}, "c": {"accept": True, "text": "維護 [幾條] CI 流程"}})
    assert doc["sections"][0]["entries"][0]["lines"] == ["負責訂單 API", "維護 [幾條] CI 流程"]
    assert service._placeholders(doc) == 1


def test_answers_text_marks_skipped_as_unknown():
    cards = [{"kind": "gap", "context": "職缺要 Kubernetes", "question": "用過嗎？"}, {"kind": "missing", "context": "", "question": "還有嗎？"}]
    txt = rv.answers_text(cards, {"0": {"choice": "沒有", "text": "", "skipped": False}, "1": {"skipped": True}})
    assert "答：沒有" in txt and "跳過" in txt


def test_docx_builds():
    import docx
    data = service.build_docx({"name": "小明", "headline": "後端工程師", "contact": "a@b.c",
                               "sections": [{"title": "技能", "entries": [{"title": "", "meta": "", "lines": ["Python、FastAPI"]}]}]})
    text = "\n".join(p.text for p in docx.Document(io.BytesIO(data)).paragraphs)
    assert "小明" in text and "Python、FastAPI" in text


# ---------- 完整流程 ----------
def test_revision_flow(client, dev_ai, files):
    since = dt.datetime.now(dt.timezone.utc)
    user, uid = register(client, f"rev-{uuid.uuid4().hex[:8]}@example.com")
    rid_resume = _resume(client, uid)
    tid = client.post(f"{API}/targets", headers=user, json={"raw_text": JD}).json()["id"]
    _drain(client, since)

    r = client.post(f"{API}/resume-revisions", headers=user, json={"resume_id": rid_resume, "target_ids": [tid], "goal": "target"})
    assert r.status_code == 202, r.text
    rev = r.json()
    assert rev["status"] == "carding" and rev["jobs"][0]["company_name"]
    _drain(client, since, ("build_revision_cards",))
    rev = client.get(f"{API}/resume-revisions/{rev['id']}", headers=user).json()
    assert rev["status"] == "answering" and rev["cards"]
    cards = rev["cards"]
    gap = next(i for i, c in enumerate(cards) if c["kind"] == "gap")
    missing = next(i for i, c in enumerate(cards) if c["kind"] == "missing")

    # 回答：亂填的選項會被清掉、超出範圍的卡片序號會被忽略
    answers = {str(gap): {"choice": cards[gap]["options"][0]["label"], "text": "在 Acme 用來部署 12 個服務"},
               str(missing): {"choice": "亂填", "text": "做了 AI 模擬面試網站"}, "99": {"text": "x"}}
    for i in range(len(cards)):
        answers.setdefault(str(i), {"skipped": True})
    rev = client.put(f"{API}/resume-revisions/{rev['id']}/answers", headers=user, json={"answers": answers}).json()
    assert "99" not in rev["answers"] and rev["answers"][str(missing)]["choice"] is None
    assert rev["answers"][str(gap)]["text"] == "在 Acme 用來部署 12 個服務"

    rev = client.post(f"{API}/resume-revisions/{rev['id']}/generate", headers=user).json()
    assert rev["status"] == "generating"
    # 產生中不能再改回答
    assert client.put(f"{API}/resume-revisions/{rev['id']}/answers", headers=user, json={"answers": {}}).status_code == 409
    _drain(client, since, ("build_revision",))
    rev = client.get(f"{API}/resume-revisions/{rev['id']}", headers=user).json()
    assert rev["status"] == "ready"
    lines = [ln for s in rev["result"]["sections"] for e in s["entries"] for ln in e["lines"]]
    new = [ln for ln in lines if ln["change"] == "new"]
    assert any("12 個服務" in ln["text"] for ln in new) and all(ln["id"] for ln in lines)

    # 還原一條新增、改寫一條
    keep = next(ln for ln in lines if ln["change"] == "keep")
    dec = {new[0]["id"]: {"accept": False}, keep["id"]: {"text": "主導訂單 API 開發，服務 [多少] 位使用者"}, "nope": {"accept": False}}
    rev = client.put(f"{API}/resume-revisions/{rev['id']}/decisions", headers=user, json={"decisions": dec}).json()
    assert set(rev["decisions"]) == {new[0]["id"], keep["id"]} and rev["placeholders"] == 1

    d = client.get(f"{API}/resume-revisions/{rev['id']}/docx", headers=user)
    assert d.status_code == 200 and d.headers["content-type"].startswith(service.DOCX)
    import docx
    text = "\n".join(p.text for p in docx.Document(io.BytesIO(d.content)).paragraphs)
    assert "服務 [多少] 位使用者" in text and new[0]["text"] not in text

    # 存成新履歷（設為主要）：原本那份不動，多一份 docx、等待解析
    rev = client.post(f"{API}/resume-revisions/{rev['id']}/save", headers=user, json={"set_primary": True}).json()
    saved = rev["saved_resume"]
    assert saved and saved["parse_status"] == "pending" and len(files) == 1
    items = client.get(f"{API}/resumes", headers=user).json()["items"]
    assert {x["id"] for x in items} == {rid_resume, saved["id"]}
    assert next(x for x in items if x["id"] == saved["id"])["is_primary"]

    hist = client.get(f"{API}/resume-revisions", headers=user).json()["items"]
    assert hist[0]["id"] == rev["id"] and hist[0]["status"] == "ready"


def test_revision_without_jobs_has_no_gap_cards(client, dev_ai):
    since = dt.datetime.now(dt.timezone.utc)
    user, uid = register(client, f"rev-{uuid.uuid4().hex[:8]}@example.com")
    rev = client.post(f"{API}/resume-revisions", headers=user, json={"resume_id": _resume(client, uid), "goal": "refresh"}).json()
    _drain(client, since, ("build_revision_cards",))
    rev = client.get(f"{API}/resume-revisions/{rev['id']}", headers=user).json()
    assert rev["status"] == "answering" and rev["jobs"] == []
    assert all(c["kind"] != "gap" for c in rev["cards"])


def test_revision_rejects_unparsed_resume_and_other_users(client, dev_ai):
    user, uid = register(client, f"rev-{uuid.uuid4().hex[:8]}@example.com")
    other, _ = register(client, f"rev-{uuid.uuid4().hex[:8]}@example.com")
    r = client.post(f"{API}/resume-revisions", headers=user, json={"resume_id": _resume(client, uid, "parsing")})
    assert r.status_code == 409 and r.json()["error"]["code"] == "RESOURCE_NOT_READY"
    rid = client.post(f"{API}/resume-revisions", headers=user, json={"resume_id": _resume(client, uid)}).json()["id"]
    assert client.get(f"{API}/resume-revisions/{rid}", headers=other).status_code == 404
    # 還沒完成不能下載、不能存
    assert client.get(f"{API}/resume-revisions/{rid}/docx", headers=user).status_code == 409


# ---------- GitHub 作品 ----------
REVIEW = {"purpose": "AI 模擬面試網站", "architecture": "FastAPI＋PostgreSQL＋arq worker", "tech_stack": ["Python", "FastAPI", "PostgreSQL"],
          "skills": [{"skill": "背景工作", "evidence": "backend/app/workers/queue.py 用 outbox"}], "practices": ["有測試"],
          "highlights": ["用 outbox＋arq 做可靠的背景工作"], "weaknesses": [], "maturity": "可上線的完整產品"}


def _evidence(url: str) -> dict:
    name = url.removeprefix("https://github.com/")
    if name.endswith("private-thing"):
        return {"url": url, "status": "failed", "error": "找不到這個 repo，可能是私人 repo 或網址打錯"}
    return {"url": url, "status": "ok", "kind": "repo", "files_read": ["a.py", "b.py"], "readme_read": True, "commits_read": 3,
            "repo": {"full_name": name, "url": url, "pushed_at": "2026-11-01T00:00:00Z", "languages": {"Python": 100}}, "analysis": REVIEW}


def test_revision_reads_github_and_writes_projects(client, dev_ai, monkeypatch):
    read = []

    async def fake_read(links, user_id):
        read.append(list(links))
        return [_evidence(u) for u in links]
    monkeypatch.setattr(service.career, "read_portfolio", fake_read)
    since = dt.datetime.now(dt.timezone.utc)
    user, uid = register(client, f"rev-{uuid.uuid4().hex[:8]}@example.com")
    rid_resume = _resume(client, uid)

    # 只收 GitHub repo 網址；同一個 repo 不同寫法算一個
    bad = client.post(f"{API}/resume-revisions", headers=user, json={"resume_id": rid_resume, "portfolio_links": ["https://gitlab.com/a/b"]})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "VALIDATION_ERROR"
    bad = client.post(f"{API}/resume-revisions", headers=user, json={"resume_id": rid_resume, "portfolio_links": ["github.com/someone"]})
    assert bad.status_code == 400
    r = client.post(f"{API}/resume-revisions", headers=user, json={"resume_id": rid_resume, "portfolio_links": [
        "github.com/me/ai-interview", "https://github.com/me/ai-interview/", "https://github.com/me/private-thing"]})
    assert r.status_code == 202, r.text
    rev = r.json()
    assert rev["setup"]["portfolio_links"] == ["https://github.com/me/ai-interview", "https://github.com/me/private-thing"]
    async def queue_of() -> str:
        async with db.connection() as conn:
            return await conn.fetchval("SELECT queue FROM background_jobs WHERE kind = 'build_revision_cards' AND payload->>'revision_id' = $1",
                                       rev["id"])
    assert client.portal.call(queue_of) == "default"         # 要先讀 GitHub：放逾時比較長的佇列

    _drain(client, since, ("build_revision_cards",))
    rev = client.get(f"{API}/resume-revisions/{rev['id']}", headers=user).json()
    assert rev["status"] == "answering" and rev["stage"] is None
    assert [p["status"] for p in rev["portfolio"]] == ["ok", "failed"] and rev["portfolio"][0]["files_read"] == 2
    assert "analysis" not in rev["portfolio"][0]                 # 完整審閱不回給前端
    first = rev["cards"][0]
    assert first["kind"] == "portfolio" and first["repo"] == "me/ai-interview" and "AI 模擬面試網站" in first["context"]
    assert sum(c["kind"] == "portfolio" for c in rev["cards"]) == 1          # 沒讀到的 repo 不出卡

    answers = {str(i): {"skipped": True} for i in range(len(rev["cards"]))}
    answers["0"] = {"choice": first["options"][0]["label"], "text": "有 50 位使用者"}
    client.put(f"{API}/resume-revisions/{rev['id']}/answers", headers=user, json={"answers": answers})
    client.post(f"{API}/resume-revisions/{rev['id']}/generate", headers=user)
    _drain(client, since, ("build_revision",))
    rev = client.get(f"{API}/resume-revisions/{rev['id']}", headers=user).json()
    assert rev["status"] == "ready"
    proj = next(s for s in rev["result"]["sections"] if s["title"] == "專案")
    assert proj["entries"][0]["title"] == "ai-interview"
    assert all(ln["change"] == "new" and ln["card"] == 0 for ln in proj["entries"][0]["lines"])
    assert len(read) == 1                                         # 產生履歷時沿用讀過的結果，不重讀


def test_revision_github_repo_not_added_when_declined(client, dev_ai, monkeypatch):
    async def fake_read(links, user_id):
        return [_evidence(u) for u in links]
    monkeypatch.setattr(service.career, "read_portfolio", fake_read)
    since = dt.datetime.now(dt.timezone.utc)
    user, uid = register(client, f"rev-{uuid.uuid4().hex[:8]}@example.com")
    rev = client.post(f"{API}/resume-revisions", headers=user,
                      json={"resume_id": _resume(client, uid), "portfolio_links": ["github.com/me/ai-interview"]}).json()
    _drain(client, since, ("build_revision_cards",))
    rev = client.get(f"{API}/resume-revisions/{rev['id']}", headers=user).json()
    answers = {str(i): {"skipped": True} for i in range(len(rev["cards"]))}
    answers["0"] = {"choice": "不要放"}
    client.put(f"{API}/resume-revisions/{rev['id']}/answers", headers=user, json={"answers": answers})
    client.post(f"{API}/resume-revisions/{rev['id']}/generate", headers=user)
    _drain(client, since, ("build_revision",))
    rev = client.get(f"{API}/resume-revisions/{rev['id']}", headers=user).json()
    assert all(s["title"] != "專案" for s in rev["result"]["sections"])


def test_github_repos_lists_public_repos(client, monkeypatch):
    async def fake_profile(owner):
        if owner == "ghost":
            raise service.github.ReadError("找不到這個 repo，可能是私人 repo 或網址打錯")
        return {"repos": [{"name": "ai-interview", "url": f"https://github.com/{owner}/ai-interview", "description": "", "language": "Python",
                           "stars": 3, "pushed_at": "2026-11-01T00:00:00Z"}]}
    monkeypatch.setattr(service.github, "read_profile", fake_profile)
    user, _ = register(client, f"rev-{uuid.uuid4().hex[:8]}@example.com")
    r = client.get(f"{API}/resume-revisions/github-repos", headers=user, params={"owner": "https://github.com/me"})
    assert r.status_code == 200 and r.json()["owner"] == "me" and r.json()["repos"][0]["name"] == "ai-interview"
    assert client.get(f"{API}/resume-revisions/github-repos", headers=user, params={"owner": "@me"}).json()["owner"] == "me"
    r = client.get(f"{API}/resume-revisions/github-repos", headers=user, params={"owner": "ghost"})
    assert r.status_code == 400 and "找不到這個 GitHub 帳號" in r.json()["error"]["message"]
    assert client.get(f"{API}/resume-revisions/github-repos", headers=user, params={"owner": "bad name!"}).status_code == 400
