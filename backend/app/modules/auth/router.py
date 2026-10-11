from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, EmailStr, Field

from app.core.config import settings
from app.modules.auth import google, service

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)
    display_name: str = Field(default="", max_length=60)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(max_length=200)


@router.post("/register", status_code=201)
async def register(body: RegisterIn, request: Request, response: Response):
    return await service.register(body.email, body.password, body.display_name, request, response)


@router.post("/login")
async def login(body: LoginIn, request: Request, response: Response):
    return await service.login(body.email, body.password, request, response)


@router.post("/refresh")
async def refresh(request: Request, response: Response):
    return await service.refresh(request, response)


@router.post("/logout", status_code=204)
async def logout(request: Request, response: Response):
    await service.logout(request, response)
    response.status_code = 204
    return None


@router.get("/providers")
async def providers():
    """登入頁用：有哪些第三方登入可以用。"""
    return {"password": True, "google": settings.google_enabled}


@router.get("/google/start", include_in_schema=True)
async def google_start(request: Request):
    return await google.start(request)


@router.get("/google/callback", include_in_schema=True)
async def google_callback(request: Request):
    return await google.callback(request)
