"""MCP 連接器測試：授權流程、state 驗證、token 加密存放、刷新、中斷連線、使用者隔離。

不需要真的 Notion：把 oauth 模組的探索／註冊／換 token 換成假的。
需要 docker compose 的 PostgreSQL 與 Redis。執行：.venv/bin/pytest tests/test_mcp_connectors.py -q
"""
import datetime as dt
import uuid
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app.core import crypto, db
from app.core.config import settings
from app.modules.mcp_connectors import oauth, service
from tests.conftest import API, register

META = {"authorization_endpoint": "https://auth.example.com/authorize", "token_endpoint": "https://auth.example.com/token",
        "registration_endpoint": "https://auth.example.com/register", "revocation_endpoint": "https://auth.example.com/revoke"}


@pytest.fixture()
def fake(monkeypatch):
    calls = {"register": 0, "exchange": [], "refresh": [], "revoke": []}
    tokens = {"access": "at-1", "refresh": "rt-1", "expires_in": 3600}

    async def discover(url):
        return META

    async def reg(meta, redirect_uri):
        calls["register"] += 1
        return {"client_id": f"client-{calls['register']}"}

    async def exchange(meta, **kw):
        calls["exchange"].append(kw)
        return {"access_token": tokens["access"], "refresh_token": tokens["refresh"], "expires_in": tokens["expires_in"],
                "workspace_name": "測試 Workspace"}

    async def refresh(meta, **kw):
        calls["refresh"].append(kw)
        if tokens.get("refresh_fail"):
            raise httpx.HTTPStatusError("bad", request=httpx.Request("POST", META["token_endpoint"]),
                                        response=httpx.Response(400, text='{"error":"invalid_grant"}'))
        return {"access_token": "at-2", "expires_in": 3600}

    async def revoke(meta, **kw):
        calls["revoke"].append(kw)

    for name, fn in {"discover": discover, "register": reg, "exchange": exchange, "refresh": refresh, "revoke": revoke}.items():
        monkeypatch.setattr(oauth, name, fn)
    return calls, tokens


def connect(client, headers):
    r = client.post(f"{API}/mcp-connectors/notion/start", headers=headers)
    assert r.status_code == 200, r.text
    url = urlsplit(r.json()["authorize_url"])
    q = {k: v[0] for k, v in parse_qs(url.query).items()}
    return q, client.get(f"{API}/mcp-connectors/notion/callback?state={q['state']}&code=abc", follow_redirects=False)


def run(client, fn, *args):
    """在 app 的事件迴圈裡執行（共用 lifespan 建好的連線池）。"""
    return client.portal.call(fn, *args)


def test_crypto_roundtrip_and_aad():
    blob = crypto.encrypt("secret-token", b"u1:notion")
    assert b"secret-token" not in blob
    assert crypto.decrypt(blob, b"u1:notion") == "secret-token"
    with pytest.raises(Exception):
        crypto.decrypt(blob, b"u2:notion")          # 搬到別人的列解不開


def test_requires_login(client):
    assert client.get(f"{API}/mcp-connectors").status_code == 401
    assert client.post(f"{API}/mcp-connectors/notion/start").status_code == 401


def test_unknown_provider(client, fake):
    h, _ = register(client, f"mcp-{uuid.uuid4().hex[:8]}@example.com")
    assert client.post(f"{API}/mcp-connectors/gmail/start", headers=h).status_code == 404


def test_connect_flow(client, fake):
    calls, _ = fake
    h, uid = register(client, f"mcp-{uuid.uuid4().hex[:8]}@example.com")
    items = client.get(f"{API}/mcp-connectors", headers=h).json()
    assert items["enabled"] is True and items["items"][0]["provider"] == "notion" and items["items"][0]["connected"] is False

    q, r = connect(client, h)
    assert q["code_challenge_method"] == "S256" and len(q["code_challenge"]) == 43
    assert q["redirect_uri"] == settings.mcp_callback("notion")
    assert q["resource"] == settings.notion_mcp_url
    assert r.status_code == 302 and r.headers["location"] == "/?mcp_connected=notion#new"
    assert calls["exchange"][0]["code"] == "abc" and len(calls["exchange"][0]["verifier"]) >= 43

    item = client.get(f"{API}/mcp-connectors", headers=h).json()["items"][0]
    assert item["connected"] is True and item["status"] == "active" and item["account_label"] == "測試 Workspace"
    assert "at-1" not in str(item) and "rt-1" not in str(item)

    async def stored():
        async with db.connection() as conn:
            return await conn.fetchrow("SELECT * FROM mcp_connections WHERE user_id = $1", uuid.UUID(uid))
    row = run(client, stored)
    assert b"at-1" not in bytes(row["access_token"]) and b"rt-1" not in bytes(row["refresh_token"])
    assert run(client, service.access_token, uuid.UUID(uid), "notion") == "at-1"


def test_state_is_one_time_and_cookie_bound(client, fake):
    h, _ = register(client, f"mcp-{uuid.uuid4().hex[:8]}@example.com")
    q, r = connect(client, h)
    assert r.headers["location"].endswith("mcp_connected=notion#new")
    # 重複使用同一個 state
    client.cookies.set("mcp_oauth_state", q["state"], path="/api/v1/mcp-connectors")
    r = client.get(f"{API}/mcp-connectors/notion/callback?state={q['state']}&code=abc", follow_redirects=False)
    assert "mcp_error=state" in r.headers["location"]
    # 沒有 Cookie（別的瀏覽器拿到授權網址）
    r = client.post(f"{API}/mcp-connectors/notion/start", headers=h)
    state = parse_qs(urlsplit(r.json()["authorize_url"]).query)["state"][0]
    client.cookies.clear()
    r = client.get(f"{API}/mcp-connectors/notion/callback?state={state}&code=abc", follow_redirects=False)
    assert "mcp_error=state" in r.headers["location"]


def test_cancelled(client, fake):
    r = client.get(f"{API}/mcp-connectors/notion/callback?error=access_denied", follow_redirects=False)
    assert r.headers["location"] == "/?mcp_error=cancelled#new"


def test_users_are_isolated(client, fake):
    h1, _ = register(client, f"mcp-{uuid.uuid4().hex[:8]}@example.com")
    connect(client, h1)
    h2, uid2 = register(client, f"mcp-{uuid.uuid4().hex[:8]}@example.com")
    assert client.get(f"{API}/mcp-connectors", headers=h2).json()["items"][0]["connected"] is False
    with pytest.raises(Exception) as e:
        run(client, service.access_token, uuid.UUID(uid2), "notion")
    assert getattr(e.value, "code", "") == "MCP_NOT_CONNECTED"


def test_refresh_and_refresh_failure(client, fake):
    calls, tokens = fake
    tokens["expires_in"] = 10          # 已在刷新邊界內
    h, uid = register(client, f"mcp-{uuid.uuid4().hex[:8]}@example.com")
    connect(client, h)
    assert run(client, service.access_token, uuid.UUID(uid), "notion") == "at-2"
    assert calls["refresh"][0]["refresh_token"] == "rt-1"
    # 換新後沒拿到新的 refresh token → 保留舊的
    async def refresh_token():
        async with db.connection() as conn:
            return await conn.fetchrow("SELECT refresh_token, expires_at FROM mcp_connections WHERE user_id = $1", uuid.UUID(uid))
    row = run(client, refresh_token)
    assert crypto.decrypt(row["refresh_token"], f"{uid}:notion".encode()) == "rt-1"
    assert row["expires_at"] > dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=30)

    # refresh token 被拒 → 狀態改成 error，提示重新連接
    tokens["refresh_fail"] = True
    async def expire():
        async with db.transaction() as conn:
            await conn.execute("UPDATE mcp_connections SET expires_at = now() WHERE user_id = $1", uuid.UUID(uid))
    run(client, expire)
    with pytest.raises(Exception) as e:
        run(client, service.access_token, uuid.UUID(uid), "notion")
    assert getattr(e.value, "code", "") == "MCP_NOT_CONNECTED"
    assert client.get(f"{API}/mcp-connectors", headers=h).json()["items"][0]["status"] == "error"


def test_disconnect(client, fake):
    calls, _ = fake
    h, uid = register(client, f"mcp-{uuid.uuid4().hex[:8]}@example.com")
    connect(client, h)
    assert client.delete(f"{API}/mcp-connectors/notion", headers=h).status_code == 204
    assert client.get(f"{API}/mcp-connectors", headers=h).json()["items"][0]["connected"] is False
    assert calls["revoke"] and calls["revoke"][0]["token"] == "rt-1"
    assert client.delete(f"{API}/mcp-connectors/notion", headers=h).status_code == 204     # 重複中斷不報錯


def test_disabled_in_prod_without_key(client, fake, monkeypatch):
    h, _ = register(client, f"mcp-{uuid.uuid4().hex[:8]}@example.com")
    monkeypatch.setattr(settings, "env", "prod")
    monkeypatch.setattr(settings, "mcp_connector_enc_key", "")
    assert client.get(f"{API}/mcp-connectors", headers=h).json() == {"enabled": False, "items": []}
    assert client.post(f"{API}/mcp-connectors/notion/start", headers=h).status_code == 404
