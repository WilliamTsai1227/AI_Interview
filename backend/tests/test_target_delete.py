"""刪除目標職缺：題庫、面試建議、這個職缺的面試紀錄與錄音都一起刪；使用者貼上的職缺連 job_posts 也刪。"""
import uuid

from app.core import db
from app.modules.targets import service
from tests.conftest import API, register

JD = ("資深後端工程師\n公司：Acme Cloud\n工作內容：\n- 設計並維護 Python API\n- 優化 PostgreSQL 查詢\n"
      "條件要求：\n- 3 年以上 FastAPI 經驗\n- 熟悉 Kubernetes 與 Docker")


def _add_session(client, uid: str, tid: str, status: str) -> str:
    async def ins() -> str:
        async with db.connection() as conn:
            t = await conn.fetchrow("SELECT job_post_id FROM target_jobs WHERE id = $1", uuid.UUID(tid))
            sid = await conn.fetchval(
                """INSERT INTO interview_sessions (user_id, target_job_id, job_post_id, length_mode, persona, language,
                                                   voice_mode, status, job_snapshot, resume_snapshot, planned_question_count)
                   VALUES ($1, $2, $3, 'quick', 'warm', 'zh', 'scripted', $4, '{}', '{}', 1) RETURNING id""",
                uuid.UUID(uid), uuid.UUID(tid), t["job_post_id"], status)
            qid = await conn.fetchval(
                """INSERT INTO session_questions (session_id, kind, order_no, followup_mode, category, difficulty, text)
                   VALUES ($1, 'main', 1, 'none', 'behavioral', 'medium', '自我介紹') RETURNING id""", sid)
            await conn.execute(
                """INSERT INTO answer_attempts (session_id, session_question_id, attempt_no, answer_started_at, audio_key)
                   VALUES ($1, $2, 1, now(), $3)""", sid, qid, f"users/{uid}/sessions/{sid}/answers/a.webm")
            return str(sid)
    return client.portal.call(ins)


def _count(client, sql: str, *args) -> int:
    async def q() -> int:
        async with db.connection() as conn:
            return await conn.fetchval(sql, *args)
    return client.portal.call(q)


def test_delete_target_removes_everything(client, monkeypatch):
    deleted: list[str] = []

    async def fake_delete(*keys):
        deleted.extend(keys)
    monkeypatch.setattr(service.storage, "delete", fake_delete)

    user, uid = register(client, f"del-{uuid.uuid4().hex[:8]}@example.com")
    t = client.post(f"{API}/targets", headers=user, json={"raw_text": JD}).json()
    tid = uuid.UUID(t["id"])
    jid = _count(client, "SELECT job_post_id FROM target_jobs WHERE id = $1", tid)
    sid = _add_session(client, uid, t["id"], "completed")

    r = client.delete(f"{API}/targets/{tid}", headers=user)
    assert r.status_code == 204, r.text

    assert _count(client, "SELECT count(*) FROM target_jobs WHERE id = $1", tid) == 0
    assert _count(client, "SELECT count(*) FROM question_sets WHERE target_job_id = $1", tid) == 0
    assert _count(client, "SELECT count(*) FROM interview_preps WHERE target_job_id = $1", tid) == 0
    assert _count(client, "SELECT count(*) FROM interview_sessions WHERE id = $1", uuid.UUID(sid)) == 0
    assert _count(client, "SELECT count(*) FROM job_posts WHERE id = $1", jid) == 0
    assert deleted == [f"users/{uid}/sessions/{sid}/answers/a.webm"]
    assert client.get(f"{API}/targets/{tid}", headers=user).status_code == 404


def test_delete_target_blocked_by_active_session_and_other_users(client):
    user, uid = register(client, f"del-{uuid.uuid4().hex[:8]}@example.com")
    other, _ = register(client, f"del-{uuid.uuid4().hex[:8]}@example.com")
    tid = client.post(f"{API}/targets", headers=user, json={"raw_text": JD}).json()["id"]

    assert client.delete(f"{API}/targets/{tid}", headers=other).status_code == 404

    _add_session(client, uid, tid, "paused")
    r = client.delete(f"{API}/targets/{tid}", headers=user)
    assert r.status_code == 409 and r.json()["error"]["code"] == "ACTIVE_SESSION_EXISTS"
    assert _count(client, "SELECT count(*) FROM target_jobs WHERE id = $1", uuid.UUID(tid)) == 1
