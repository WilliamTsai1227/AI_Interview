"""密碼雜湊（argon2）、Access JWT、Refresh Token（DB 存 SHA-256）、目前使用者。"""
import asyncio
import datetime as dt
import hashlib
import secrets
from dataclasses import dataclass
from uuid import UUID

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Depends, Request

from app.core.config import settings
from app.core.errors import AppError

_ph = PasswordHasher()


# argon2 刻意很吃 CPU（每次約 50 ms）。放到執行緒算，才不會卡住事件迴圈裡的其他請求；
# argon2 的 C 程式計算時會釋放 GIL，多個登入可以真的平行。（壓力測試發現：直接算時登入尖峰會把所有 API 拖到 400 ms）
async def hash_password(pw: str) -> str:
    return await asyncio.to_thread(_ph.hash, pw)


async def verify_password(hashed: str | None, pw: str) -> bool:
    if not hashed:
        return False
    return await asyncio.to_thread(_verify, hashed, pw)


def _verify(hashed: str, pw: str) -> bool:
    try:
        return _ph.verify(hashed, pw)
    except VerifyMismatchError:
        return False
    except Exception:
        return False


def create_access_token(user_id: UUID, role: str) -> str:
    now = dt.datetime.now(dt.timezone.utc)
    payload = {"sub": str(user_id), "role": role, "iat": now,
               "exp": now + dt.timedelta(minutes=settings.access_token_minutes)}
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def new_refresh_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(48)
    return token, sha256(token)


def sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


@dataclass(frozen=True)
class CurrentUser:
    id: UUID
    role: str


def current_user(request: Request) -> CurrentUser:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise AppError(401, "UNAUTHENTICATED", "請先登入")
    try:
        data = jwt.decode(auth[7:], settings.jwt_secret, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise AppError(401, "TOKEN_EXPIRED", "登入已過期")
    except jwt.PyJWTError:
        raise AppError(401, "UNAUTHENTICATED", "請先登入")
    user = CurrentUser(UUID(data["sub"]), data.get("role", "user"))
    request.state.user_id = user.id
    return user


def admin_user(user: CurrentUser = Depends(current_user)) -> CurrentUser:
    if user.role != "admin":
        raise AppError(403, "FORBIDDEN", "需要管理員權限")
    return user
