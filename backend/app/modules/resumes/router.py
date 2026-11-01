from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.security import CurrentUser, current_user
from app.modules.resumes import service

router = APIRouter(prefix="/resumes", tags=["resumes"])


class ResumeIn(BaseModel):
    upload_id: UUID
    set_primary: bool | None = None


class ResumePatch(BaseModel):
    is_primary: bool | None = None
    filename: str | None = Field(default=None, max_length=200)


@router.post("")
async def create(body: ResumeIn, response: Response, user: CurrentUser = Depends(current_user)):
    status, data = await service.create(user.id, body.upload_id, body.set_primary)
    response.status_code = status
    return data


@router.get("")
async def list_(user: CurrentUser = Depends(current_user)):
    return {"items": await service.list_(user.id), "max": settings.resume_max_count}


@router.get("/{resume_id}")
async def get(resume_id: UUID, user: CurrentUser = Depends(current_user)):
    return await service.get(user.id, resume_id)


@router.get("/{resume_id}/file")
async def file(resume_id: UUID, user: CurrentUser = Depends(current_user)):
    data, mime, name = await service.file(user.id, resume_id)
    return Response(data, media_type=mime, headers={
        "Content-Disposition": f"inline; filename*=UTF-8''{quote(name)}", "Cache-Control": "private, no-store"})


@router.get("/{resume_id}/pages/{n}")
async def page(resume_id: UUID, n: int, user: CurrentUser = Depends(current_user)):
    # 履歷內容建立後不會變（換檔是新的一份），可以讓瀏覽器快取
    return Response(await service.page_image(user.id, resume_id, n), media_type="image/webp",
                    headers={"Cache-Control": "private, max-age=86400"})


@router.get("/{resume_id}/preview")
async def preview(resume_id: UUID, user: CurrentUser = Depends(current_user)):
    return await service.preview(user.id, resume_id)


@router.patch("/{resume_id}")
async def patch(resume_id: UUID, body: ResumePatch, user: CurrentUser = Depends(current_user)):
    return await service.patch(user.id, resume_id, body.is_primary, body.filename)


@router.delete("/{resume_id}", status_code=204)
async def delete(resume_id: UUID, user: CurrentUser = Depends(current_user)):
    await service.delete(user.id, resume_id)
    return Response(status_code=204)
