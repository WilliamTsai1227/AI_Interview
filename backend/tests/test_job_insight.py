"""職缺解讀：加入職缺後產生、依新內容重新出題時重新產生、失敗可重試、舊職缺第一次打開時補產生。"""
import datetime as dt
import uuid

import pytest

from app.ai.agents import insight as ai_insight
from app.core import db
from app.core.config import settings
from app.workers import main as worker
from tests.conftest import API, register

JD = ("(Sr.) AI Engineer\n公司：Acme Security\n工作內容：\n- Build and operate the AI infrastructure — agents, evaluation harnesses, telemetry\n"
      "- Drive measurable adoption with clear metrics\n條件要求：\n- Proficiency in Python\n- Experience in multi-agent systems")


@pytest.fixture()
def dev_ai(monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "")      # 假 AI


def _drain(client, since: dt.datetime) -> None:
    async def run_once() -> int:
        async with db.connection() as conn:
            jobs = await conn.fetch(
                "SELECT id, kind, payload FROM background_jobs WHERE status IN ('queued','retrying') AND created_at >= $1 ORDER BY id", since)
        for j in jobs:
            await worker.HANDLERS[j["kind"]](j["payload"])
            async with db.connection() as conn:
                await conn.execute("UPDATE background_jobs SET status = 'succeeded', finished_at = now() WHERE id = $1", j["id"])
        return len(jobs)
    for _ in range(10):
        if not client.portal.call(run_once):
            return


def _db_now(client) -> dt.datetime:
    """起點用資料庫的時鐘：background_jobs.created_at 是資料庫的 now()，Docker VM 和主機的時鐘可能差幾毫秒，
    用主機時間當起點會漏掉剛建立的工作。"""
    async def q():
        async with db.connection() as conn:
            return await conn.fetchval("SELECT clock_timestamp()")
    return client.portal.call(q)


def _sql(client, sql: str, *args):
    async def q():
        async with db.connection() as conn:
            return await conn.execute(sql, *args)
    return client.portal.call(q)


def test_insight_generated_after_parse_and_on_regenerate(client, dev_ai):
    since = _db_now(client)
    user, _ = register(client, f"ins-{uuid.uuid4().hex[:8]}@example.com")
    tid = client.post(f"{API}/targets", headers=user, json={"raw_text": JD}).json()["id"]
    _drain(client, since)

    t = client.get(f"{API}/targets/{tid}", headers=user).json()
    ins = t["job"]["insight"]
    assert ins["status"] == "ready", ins
    d = ins["data"]
    assert d["essence"] and d["focus"] and all(f["title"] and f["meaning"] for f in d["focus"])
    assert all(e["kind"] in ("must", "plus") for e in d["experiences"])
    # 列表不帶解讀（只有詳情才有）
    assert "insight" not in client.get(f"{API}/targets", headers=user).json()["items"][0]["job"]

    # 依新內容重新出題 → 解讀重新產生
    client.patch(f"{API}/targets/{tid}", headers=user, json={"raw_text": JD + "\n- Terraform"})
    since = _db_now(client)
    assert client.post(f"{API}/targets/{tid}/regenerate", headers=user).status_code == 202
    _drain(client, since)
    t = client.get(f"{API}/targets/{tid}", headers=user).json()
    assert t["job"]["insight"]["status"] == "ready"


def test_insight_failure_can_retry_and_old_jobs_backfill(client, dev_ai, monkeypatch):
    since = _db_now(client)
    user, _ = register(client, f"ins-{uuid.uuid4().hex[:8]}@example.com")
    tid = client.post(f"{API}/targets", headers=user, json={"raw_text": JD}).json()["id"]

    async def boom(job, *, user_id=None):
        raise RuntimeError("AI down")
    monkeypatch.setattr(ai_insight, "analyze", boom)
    _drain(client, since)
    t = client.get(f"{API}/targets/{tid}", headers=user).json()
    assert t["job"]["insight"] == {"status": "failed"}

    monkeypatch.undo()
    monkeypatch.setattr(settings, "openai_api_key", "")
    since = _db_now(client)
    r = client.post(f"{API}/targets/{tid}/insight", headers=user)
    assert r.status_code == 200 and r.json()["job"]["insight"]["status"] == "pending"
    assert client.post(f"{API}/targets/{tid}/insight", headers=user).status_code == 409
    _drain(client, since)
    assert client.get(f"{API}/targets/{tid}", headers=user).json()["job"]["insight"]["status"] == "ready"

    # 功能上線前加入的職缺（沒有解讀）：第一次打開時補產生
    _sql(client, "DELETE FROM job_insights WHERE job_post_id = (SELECT job_post_id FROM target_jobs WHERE id = $1)", uuid.UUID(tid))
    since = _db_now(client)
    assert client.get(f"{API}/targets/{tid}", headers=user).json()["job"]["insight"]["status"] == "pending"
    _drain(client, since)
    assert client.get(f"{API}/targets/{tid}", headers=user).json()["job"]["insight"]["status"] == "ready"
