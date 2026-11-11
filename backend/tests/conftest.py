"""共用 fixture：管理後台的完整登入（名單內 Email＋Google 登入＋後台帳密）。"""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.security import create_access_token
from app.main import app

API = "/api/v1"
CONSOLE_USER, CONSOLE_PW = "boss", "console-pass-123"


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(settings, "rate_limits_enabled", False)
    with TestClient(app, base_url="http://localhost:8009") as c:
        yield c


def register(c: TestClient, email: str) -> tuple[dict, str]:
    r = c.post(f"{API}/auth/register", json={"email": email, "password": "Passw0rd!x", "display_name": "測試"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}, r.json()["user"]["id"]


def google_headers(uid: str) -> dict:
    """等同用 Google 登入後拿到的 access token（amr = google）。"""
    return {"Authorization": f"Bearer {create_access_token(uuid.UUID(uid), 'google')}"}


@pytest.fixture()
def console(client, monkeypatch):
    """回傳 (admin_email, uid, 已通過三關的 headers)。"""
    email = f"boss-{uuid.uuid4().hex[:8]}@example.com"
    monkeypatch.setattr(settings, "admin_google_emails", f"someone@else.com, {email.upper()}")
    monkeypatch.setattr(settings, "admin_console_username", CONSOLE_USER)
    monkeypatch.setattr(settings, "admin_console_password", CONSOLE_PW)
    _, uid = register(client, email)
    g = google_headers(uid)
    r = client.post(f"{API}/admin/console/login", headers=g, json={"username": CONSOLE_USER, "password": CONSOLE_PW})
    assert r.status_code == 200, r.text
    return email, uid, g | {"X-Admin-Session": r.json()["session"]}
