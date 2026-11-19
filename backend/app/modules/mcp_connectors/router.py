"""MCP 連接器 API（spec/features/mcp-connectors.md）。token 不會出現在任何回應裡。"""
from fastapi import APIRouter, Depends, Request, Response

from app.core.security import CurrentUser, current_user
from app.modules.mcp_connectors import service

router = APIRouter(prefix="/mcp-connectors", tags=["mcp-connectors"])


@router.get("")
async def list_connectors(user: CurrentUser = Depends(current_user)):
    return await service.list_for(user.id)


@router.post("/{provider}/start")
async def start(provider: str, response: Response, user: CurrentUser = Depends(current_user)):
    """回傳授權網址；前端整頁導過去（不能用 302：access token 在 Authorization 標頭，整頁導向帶不到）。"""
    return await service.start(user.id, provider, response)


@router.get("/{provider}/callback", include_in_schema=True)
async def callback(provider: str, request: Request):
    return await service.callback(provider, request)


@router.delete("/{provider}", status_code=204)
async def disconnect(provider: str, user: CurrentUser = Depends(current_user)):
    await service.disconnect(user.id, provider)
    return Response(status_code=204)
