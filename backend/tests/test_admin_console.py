"""管理後台的進入門檻：名單內 Email × Google 登入 × 後台帳密，三個缺一不可。

需要 docker compose 的 PostgreSQL 與 Redis。執行：.venv/bin/pytest tests/test_admin_console.py -q
"""
import uuid

import jwt

from app.core.config import settings
from tests.conftest import API, CONSOLE_PW, CONSOLE_USER, google_headers, register
from tests.test_google_auth import login_via_google

LOGIN = f"{API}/admin/console/login"
GOOD = {"username": CONSOLE_USER, "password": CONSOLE_PW}


def test_full_gate_passes(client, console):
    _, _, h = console
    assert client.get(f"{API}/me", headers=h).json()["admin_console"] is True
    assert client.get(f"{API}/admin/plans", headers=h).status_code == 200
    assert client.get(f"{API}/admin/users", headers=h).status_code == 200
    # 職缺匯入／審核也在同一道門後面
    assert client.get(f"{API}/admin/jobs/review-queue", headers=h).status_code == 200
    assert client.get(f"{API}/admin/job-ingest-runs", headers=h).status_code == 200


def test_job_admin_apis_need_console(client, console):
    user, _ = register(client, f"plain-{uuid.uuid4().hex[:8]}@example.com")
    for method, path in [("GET", "/admin/jobs/review-queue"), ("GET", "/admin/job-ingest-runs"),
                         ("POST", "/admin/jobs/import"), ("PATCH", f"/admin/jobs/{uuid.uuid4()}/review")]:
        r = client.request(method, f"{API}{path}", headers=user, json=[] if method == "POST" else {"review_status": "published"})
        assert r.status_code == 404, (path, r.status_code)
    assert "role" not in client.get(f"{API}/me", headers=user).json()["user"]


def test_disabled_when_not_configured(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_console_password", "")
    _, uid = register(client, f"x-{uuid.uuid4().hex[:8]}@example.com")
    g = google_headers(uid)
    assert client.get(f"{API}/me", headers=g).json()["admin_console"] is False
    assert client.get(f"{API}/admin/console/status", headers=g).status_code == 404


def test_password_login_is_not_enough(client, console):
    email, uid, _ = console
    r = client.post(f"{API}/auth/login", json={"email": email, "password": "Passw0rd!x"})
    pw = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert client.get(f"{API}/me", headers=pw).json()["admin_console"] is False
    r = client.get(f"{API}/admin/console/status", headers=pw)
    assert r.json() | {"session_minutes": None} == {"eligible": False, "reason": "google_required", "session_minutes": None}
    r = client.post(LOGIN, headers=pw, json=GOOD)
    assert r.status_code == 403 and r.json()["error"]["code"] == "GOOGLE_LOGIN_REQUIRED"
    # 就算拿到別人的後台工作階段，密碼登入的 token 也不行
    _, _, h = console
    assert client.get(f"{API}/admin/plans", headers=pw | {"X-Admin-Session": h["X-Admin-Session"]}).status_code == 403


def test_not_allowlisted_google_user_gets_404(client, console):
    _, uid = register(client, f"other-{uuid.uuid4().hex[:8]}@example.com")
    g = google_headers(uid)
    assert client.get(f"{API}/me", headers=g).json()["admin_console"] is False
    assert client.post(LOGIN, headers=g, json=GOOD).status_code == 404
    assert client.get(f"{API}/admin/plans", headers=g).status_code == 404


def test_session_required_and_bound(client, console, monkeypatch):
    _, uid, h = console
    g = google_headers(uid)
    r = client.get(f"{API}/admin/plans", headers=g)
    assert r.status_code == 403 and r.json()["error"]["code"] == "ADMIN_SESSION_REQUIRED"
    assert client.post(LOGIN, headers=g, json={"username": CONSOLE_USER, "password": "wrong"}).status_code == 400
    # 後台工作階段不能當成登入用的 access token
    assert client.get(f"{API}/me", headers={"Authorization": f"Bearer {h['X-Admin-Session']}"}).status_code == 401
    # 換掉 .env 裡的後台密碼 → 舊的工作階段立刻失效
    monkeypatch.setattr(settings, "admin_console_password", "rotated-password")
    assert client.get(f"{API}/admin/plans", headers=h).status_code == 403


def test_lockout_after_failures(client, console):
    _, uid, _ = console
    g = google_headers(uid)
    for _ in range(5):
        assert client.post(LOGIN, headers=g, json={"username": "x", "password": "y"}).status_code == 400
    assert client.post(LOGIN, headers=g, json=GOOD).status_code == 429


def test_google_login_survives_refresh(client, monkeypatch):
    """真的走 Google callback：登入方式記在 refresh token 上，換發後的 access token 仍是 amr = google。"""
    monkeypatch.setattr(settings, "google_client_id", "test-client.apps.googleusercontent.com")
    monkeypatch.setattr(settings, "google_client_secret", "test-secret")
    email = f"g-{uuid.uuid4().hex[:8]}@example.com"
    monkeypatch.setattr(settings, "admin_google_emails", email)
    monkeypatch.setattr(settings, "admin_console_username", CONSOLE_USER)
    monkeypatch.setattr(settings, "admin_console_password", CONSOLE_PW)
    r = login_via_google(client, monkeypatch, {"sub": uuid.uuid4().hex, "email": email, "email_verified": True, "name": "G"})
    assert r.status_code == 302
    for _ in range(2):                       # 換發兩次都保留
        tok = client.post(f"{API}/auth/refresh").json()["access_token"]
        assert jwt.decode(tok, options={"verify_signature": False})["amr"] == "google"
    h = {"Authorization": f"Bearer {tok}"}
    assert client.get(f"{API}/me", headers=h).json()["admin_console"] is True
    assert client.post(LOGIN, headers=h, json=GOOD).status_code == 200
