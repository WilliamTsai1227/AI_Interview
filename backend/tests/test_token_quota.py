"""會員方案與 token 額度：/me 顯示用量、入口軟上限、LLMClient 硬上限、方案到期、管理後台 API。

需要 docker compose 的 PostgreSQL 與 Redis。執行：.venv/bin/pytest tests/test_token_quota.py -q
"""
import datetime as dt
import uuid

import pytest

from app.ai import client as ai
from app.modules.membership import quota
from tests.conftest import API, register

JD = "資深後端工程師\n公司：Acme\n工作內容：設計與維運 Python API、PostgreSQL 調校、雲端部署。\n條件：3 年以上 FastAPI 經驗。"


@pytest.fixture()
def users(client, console):
    user, uid = register(client, f"quota-{uuid.uuid4().hex[:8]}@example.com")
    return user, console[2], uid


def _set(client, admin, uid, **body):
    r = client.patch(f"{API}/admin/users/{uid}/membership", headers=admin, json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_me_shows_plan_and_usage(client, users):
    user, _, _ = users
    me = client.get(f"{API}/me", headers=user).json()
    assert me["membership"]["plan"] == "free"
    assert me["usage"]["tokens_used"] == 0
    assert me["usage"]["tokens_limit"] == 300000
    assert me["usage"]["interviews_limit"] == 10


def test_soft_limit_blocks_new_ai_work(client, users):
    user, admin, uid = users
    _set(client, admin, uid, token_limit_override=0, reason="test")
    r = client.post(f"{API}/targets", headers=user, json={"raw_text": JD})
    assert r.status_code == 403 and r.json()["error"]["code"] == "TOKEN_QUOTA_EXCEEDED"
    assert r.json()["error"]["details"]["limit"] == 0
    r = client.post(f"{API}/career/plans", headers=user, json={"target_ids": []})
    assert r.json()["error"]["code"] == "TOKEN_QUOTA_EXCEEDED"
    # 只讀的 API 照常可用
    assert client.get(f"{API}/targets", headers=user).status_code == 200

    # 換成不限額度的方案、清掉覆寫 → 放行
    out = _set(client, admin, uid, plan="unlimited", token_limit_override=None)
    assert out["tokens_limit"] is None
    r = client.post(f"{API}/targets", headers=user, json={"raw_text": JD})
    assert r.status_code in (200, 202), r.text


def test_usage_recorded_and_hard_limit(client, users):
    _, admin, uid = users
    u = uuid.UUID(uid)
    _set(client, admin, uid, token_limit_override=1000)   # 硬上限 = 1000 × 1.2 = 1200

    async def scenario():
        from app.core import db
        async with db.transaction() as conn:
            await quota.record(conn, u, 900, 100, 50)     # 剛好到軟上限
        await quota.check_hard(u, "question_gen")          # 1000 < 1200 → 還能跑
        async with db.transaction() as conn:
            await quota.record(conn, u, 150, 50)           # 1200
        with pytest.raises(quota.QuotaExceeded):
            await quota.check_hard(u, "question_gen")
        await quota.check_hard(u, "evaluate")              # 面試收尾不受硬上限
        with pytest.raises(ai.QuotaExceeded):              # LLMClient 在呼叫前就擋（開發模式的假資料也一樣）
            await ai.structured(purpose="jd_parse", schema=quota_schema(), system="s", user="u",
                                fake=lambda: quota_schema()(ok=True), user_id=u)
        assert issubclass(ai.QuotaExceeded, ai.AIError)

    client.portal.call(scenario)
    usage = client.get(f"{API}/admin/users/{uid}/usage", headers=admin).json()
    assert usage["membership"]["tokens_used"] == 1200
    assert usage["membership"]["hard_limit"] == 1200
    assert usage["history"][0]["call_count"] == 2
    assert usage["plan_changes"][0]["token_limit_override"] == 1000
    over = client.get(f"{API}/admin/users", headers=admin, params={"over_quota": "true", "limit": 200}).json()
    assert any(i["id"] == uid for i in over["items"])


def test_plan_expiry_falls_back_to_free(client, users):
    user, admin, uid = users
    past = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)).isoformat()
    _set(client, admin, uid, plan="pro", plan_expires_at=past)
    me = client.get(f"{API}/me", headers=user).json()
    assert me["user"]["plan"] == "pro" and me["membership"]["plan"] == "free"
    assert me["usage"]["tokens_limit"] == 300000


def test_admin_only_and_plan_validation(client, users):
    user, admin, uid = users
    assert client.get(f"{API}/admin/plans", headers=user).status_code == 404   # 不在名單：不透露後台存在
    plans = {p["code"]: p for p in client.get(f"{API}/admin/plans", headers=admin).json()["items"]}
    assert plans["free"]["monthly_token_limit"] == 300000 and plans["unlimited"]["monthly_token_limit"] is None
    r = client.patch(f"{API}/admin/users/{uid}/membership", headers=admin, json={"plan": "nope"})
    assert r.status_code == 400
    code = f"t_{uuid.uuid4().hex[:8]}"
    r = client.put(f"{API}/admin/plans/{code}", headers=admin,
                   json={"name": "Team", "monthly_token_limit": 5_000_000, "monthly_interview_limit": 100, "is_active": False})
    assert r.status_code == 200 and r.json()["is_active"] is False
    r = client.patch(f"{API}/admin/users/{uid}/membership", headers=admin, json={"plan": code})
    assert r.status_code == 400   # 停用的方案不能指派

    async def cleanup():
        from app.core import db
        async with db.connection() as conn:
            await conn.execute("DELETE FROM plans WHERE code = $1", code)
    client.portal.call(cleanup)


def quota_schema():
    from pydantic import BaseModel

    class Out(BaseModel):
        ok: bool
    return Out
