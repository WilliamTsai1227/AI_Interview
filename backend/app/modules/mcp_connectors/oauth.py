"""MCP Authorization（OAuth 2.1）的通用部分：探索、Dynamic Client Registration、換 token、刷新、撤銷。

流程（MCP 規範）：
1. GET <server 網域>/.well-known/oauth-protected-resource<server 路徑> → authorization_servers
2. GET <authorization server>/.well-known/oauth-authorization-server → 各端點
3. POST registration_endpoint 註冊 client（public client，PKCE）
4. Authorization Code + PKCE（S256），token 請求帶 resource（RFC 8707）

這裡的函式都不碰資料庫，方便測試時整個換掉。
"""
from urllib.parse import urlsplit

import httpx

TIMEOUT = 10
CLIENT_NAME = "AI Interview"


class OAuthError(Exception):
    pass


def _https(url: str) -> str:
    u = urlsplit(url)
    if u.scheme != "https" or not u.netloc:
        raise OAuthError(f"授權伺服器網址必須是 https：{url!r}")
    return url


async def discover(server_url: str) -> dict:
    """回傳授權伺服器 metadata（authorization_endpoint、token_endpoint、registration_endpoint …）。"""
    u = urlsplit(server_url)
    origin = f"{u.scheme}://{u.netloc}"
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        r = await c.get(f"{origin}/.well-known/oauth-protected-resource{u.path.rstrip('/')}")
        if r.status_code == 404:
            r = await c.get(f"{origin}/.well-known/oauth-protected-resource")
        r.raise_for_status()
        servers = r.json().get("authorization_servers") or [origin]
        issuer = _https(servers[0]).rstrip("/")
        m = await c.get(f"{issuer}/.well-known/oauth-authorization-server")
        m.raise_for_status()
        meta = m.json()
    for k in ("authorization_endpoint", "token_endpoint"):
        if not meta.get(k):
            raise OAuthError(f"授權伺服器缺少 {k}")
        _https(meta[k])
    if "S256" not in meta.get("code_challenge_methods_supported", ["S256"]):
        raise OAuthError("授權伺服器不支援 PKCE S256")
    return meta


async def register(meta: dict, redirect_uri: str) -> dict:
    """Dynamic Client Registration（RFC 7591）→ {client_id, client_secret?}。"""
    endpoint = meta.get("registration_endpoint")
    if not endpoint:
        raise OAuthError("授權伺服器不支援 Dynamic Client Registration")
    body = {"client_name": CLIENT_NAME, "redirect_uris": [redirect_uri],
            "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
            "token_endpoint_auth_method": "none"}
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        r = await c.post(_https(endpoint), json=body)
        r.raise_for_status()
        data = r.json()
    if not data.get("client_id"):
        raise OAuthError("註冊回應缺少 client_id")
    return data


def _client_auth(client_id: str, client_secret: str | None) -> dict:
    return {"client_id": client_id, **({"client_secret": client_secret} if client_secret else {})}


async def _token(meta: dict, form: dict) -> dict:
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        r = await c.post(meta["token_endpoint"], data=form, headers={"Accept": "application/json"})
        r.raise_for_status()
        data = r.json()
    if not data.get("access_token"):
        raise OAuthError("token 回應缺少 access_token")
    return data


async def exchange(meta: dict, *, code: str, verifier: str, redirect_uri: str, resource: str,
                   client_id: str, client_secret: str | None) -> dict:
    return await _token(meta, {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
                               "code_verifier": verifier, "resource": resource,
                               **_client_auth(client_id, client_secret)})


async def refresh(meta: dict, *, refresh_token: str, resource: str, client_id: str, client_secret: str | None) -> dict:
    return await _token(meta, {"grant_type": "refresh_token", "refresh_token": refresh_token, "resource": resource,
                               **_client_auth(client_id, client_secret)})


async def revoke(meta: dict, *, token: str, client_id: str, client_secret: str | None) -> None:
    """盡力撤銷（RFC 7009）；失敗不影響中斷連線。"""
    endpoint = meta.get("revocation_endpoint")
    if not endpoint:
        return
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        await c.post(endpoint, data={"token": token, **_client_auth(client_id, client_secret)})
