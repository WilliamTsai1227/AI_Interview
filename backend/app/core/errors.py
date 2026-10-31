"""統一錯誤格式（api.md §1.3）：{"error": {code, message, details, request_id}}。"""
from typing import Any

import asyncpg
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class AppError(Exception):
    def __init__(self, status: int, code: str, message: str, details: dict | None = None,
                 headers: dict[str, str] | None = None):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message
        self.details = details or {}
        self.headers = headers or {}


def not_found(message: str = "找不到這個項目") -> AppError:
    return AppError(404, "NOT_FOUND", message)


def state_conflict(current_version: int | None = None, message: str = "面試狀態已變更，請重新取得目前狀態") -> AppError:
    return AppError(409, "STATE_CONFLICT", message, {"current_version": current_version} if current_version is not None else {})


def bad_request(message: str, fields: dict[str, str] | None = None) -> AppError:
    return AppError(400, "VALIDATION_ERROR", message, {"fields": fields or {}})


def _body(request: Request, code: str, message: str, details: Any = None) -> dict:
    return {"error": {"code": code, "message": message, "details": details or {},
                      "request_id": getattr(request.state, "request_id", None)}}


def install(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError):
        return JSONResponse(_body(request, exc.code, exc.message, exc.details), status_code=exc.status, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        fields = {".".join(str(p) for p in e["loc"][1:]) or "body": e["msg"] for e in exc.errors()}
        return JSONResponse(_body(request, "VALIDATION_ERROR", "有欄位格式不正確", {"fields": fields}), status_code=400)

    @app.exception_handler(asyncpg.CharacterNotInRepertoireError)
    @app.exception_handler(asyncpg.UntranslatableCharacterError)
    async def _bad_chars(request: Request, exc: Exception):
        # 例如內容含有 \u0000：PostgreSQL 的 text 不收，回 400 而不是 500
        return JSONResponse(_body(request, "VALIDATION_ERROR", "內容含有無法儲存的特殊字元"), status_code=400)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):
        code = {401: "UNAUTHENTICATED", 404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(exc.status_code, "HTTP_ERROR")
        msg = {401: "請先登入", 404: "找不到這個項目"}.get(exc.status_code, str(exc.detail))
        return JSONResponse(_body(request, code, msg), status_code=exc.status_code)
