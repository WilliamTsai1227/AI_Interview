"""Google 登入測試：導向參數、state 驗證、帳號建立與連結規則。

不需要真的 Google 憑證：設定假的 client id，並把「授權碼換使用者資料」換成假資料。
需要 docker compose 的 PostgreSQL 與 Redis。執行：.venv/bin/pytest tests/test_google_auth.py -q
"""
import uuid
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.modules.auth import google


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(settings, "google_client_id", "test-client.apps.googleusercontent.com")
    monkeypatch.setattr(settings, "google_client_secret", "test-secret")
    with TestClient(app, base_url="http://localhost:8000") as c:
        yield c


def login_via_google(client, monkeypatch, info: dict):
    r = client.get("/api/v1/auth/google/start", follow_redirects=False)
    state = parse_qs(urlsplit(r.headers["location"]).query)["state"][0]

    async def fake_exchange(code, verifier):
        assert code == "abc" and len(verifier) >= 43
        return info
    monkeypatch.setattr(google, "_exchange", fake_exchange)
    return client.get(f"/api/v1/auth/google/callback?state={state}&code=abc", follow_redirects=False)


def test_providers_and_disabled(monkeypatch):
    monkeypatch.setattr(settings, "google_client_id", "")
    with TestClient(app) as c:
        assert c.get("/api/v1/auth/providers").json()["google"] is False
        assert c.get("/api/v1/auth/google/start", follow_redirects=False).status_code == 404


def test_start_redirect(client):
    assert client.get("/api/v1/auth/providers").json()["google"] is True
    r = client.get("/api/v1/auth/google/start", follow_redirects=False)
    assert r.status_code == 302
    u = urlsplit(r.headers["location"])
    q = {k: v[0] for k, v in parse_qs(u.query).items()}
    assert u.netloc == "accounts.google.com"
    assert q["client_id"] == settings.google_client_id
    assert q["redirect_uri"] == "http://localhost:8000/api/v1/auth/google/callback"
    assert q["code_challenge_method"] == "S256" and len(q["code_challenge"]) == 43
    assert q["scope"] == "openid email profile"
    sc = r.headers["set-cookie"].lower()
    assert "oauth_state=" in sc and "samesite=lax" in sc and "httponly" in sc


def test_callback_rejects_bad_state(client):
    client.get("/api/v1/auth/google/start", follow_redirects=False)
    r = client.get("/api/v1/auth/google/callback?state=forged&code=abc", follow_redirects=False)
    assert r.headers["location"] == "/?auth_error=state"
    r = client.get("/api/v1/auth/google/callback?error=access_denied", follow_redirects=False)
    assert r.headers["location"] == "/?auth_error=cancelled"


def test_state_is_single_use(client, monkeypatch):
    r = client.get("/api/v1/auth/google/start", follow_redirects=False)
    state = parse_qs(urlsplit(r.headers["location"]).query)["state"][0]

    async def fake_exchange(code, verifier):
        return {"sub": uuid.uuid4().hex, "email": f"g-{uuid.uuid4().hex[:8]}@example.com", "email_verified": True}
    monkeypatch.setattr(google, "_exchange", fake_exchange)
    assert client.get(f"/api/v1/auth/google/callback?state={state}&code=abc", follow_redirects=False).headers["location"] == "/#new"
    client.cookies.set("oauth_state", state, path="/api/v1/auth/google")
    assert client.get(f"/api/v1/auth/google/callback?state={state}&code=abc", follow_redirects=False).headers["location"] == "/?auth_error=state"


def test_new_user_then_same_google_account(client, monkeypatch):
    sub, email = uuid.uuid4().hex, f"g-{uuid.uuid4().hex[:8]}@example.com"
    r = login_via_google(client, monkeypatch, {"sub": sub, "email": email, "email_verified": True, "name": "林 小華"})
    assert r.status_code == 302 and r.headers["location"] == "/#new"
    tok = client.post("/api/v1/auth/refresh").json()["access_token"]
    me = client.get("/api/v1/me", headers={"Authorization": f"Bearer {tok}"}).json()
    assert me["user"]["email"] == email and me["user"]["display_name"] == "林 小華"
    assert me["user"]["has_password"] is False and me["user"]["providers"] == ["google"]
    # 用密碼登入 Google 帳號 → 提示改用 Google
    r = client.post("/api/v1/auth/login", json={"email": email, "password": "whatever1"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "USE_GOOGLE"
    # 同一個 Google 帳號再登入 → 同一個使用者
    client.cookies.clear()
    login_via_google(client, monkeypatch, {"sub": sub, "email": email, "email_verified": True})
    tok = client.post("/api/v1/auth/refresh").json()["access_token"]
    assert client.get("/api/v1/me", headers={"Authorization": f"Bearer {tok}"}).json()["user"]["id"] == me["user"]["id"]
    # 沒有密碼的帳號刪除：要輸入 DELETE
    h = {"Authorization": f"Bearer {tok}"}
    assert client.request("DELETE", "/api/v1/me", json={"confirm": "nope"}, headers=h).status_code == 400
    assert client.request("DELETE", "/api/v1/me", json={"confirm": "DELETE"}, headers=h).status_code == 202


def test_links_existing_password_account_and_blocks_preregistration(client, monkeypatch):
    email = f"pw-{uuid.uuid4().hex[:8]}@example.com"
    r = client.post("/api/v1/auth/register", json={"email": email, "password": "attacker123"})
    uid = r.json()["user"]["id"]
    client.cookies.clear()
    # 本人用 Google 登入同一個 Email：連結到既有帳號，但未驗證帳號的密碼被清除、舊登入失效
    login_via_google(client, monkeypatch, {"sub": uuid.uuid4().hex, "email": email.upper(), "email_verified": True})
    tok = client.post("/api/v1/auth/refresh").json()["access_token"]
    me = client.get("/api/v1/me", headers={"Authorization": f"Bearer {tok}"}).json()
    assert me["user"]["id"] == uid and me["user"]["has_password"] is False
    assert client.post("/api/v1/auth/login", json={"email": email, "password": "attacker123"}).status_code == 401


def test_unverified_google_email_rejected(client, monkeypatch):
    r = login_via_google(client, monkeypatch, {"sub": uuid.uuid4().hex, "email": "x@example.com", "email_verified": False})
    assert r.headers["location"] == "/?auth_error=unverified"
