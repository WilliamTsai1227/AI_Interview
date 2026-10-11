from uuid import UUID

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field

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
    return {"items": await service.list_(user.id)}


@router.get("/{resume_id}")
async def get(resume_id: UUID, user: CurrentUser = Depends(current_user)):
    return await service.get(user.id, resume_id)


@router.patch("/{resume_id}")
async def patch(resume_id: UUID, body: ResumePatch, user: CurrentUser = Depends(current_user)):
    return await service.patch(user.id, resume_id, body.is_primary, body.filename)


@router.delete("/{resume_id}", status_code=204)
async def delete(resume_id: UUID, user: CurrentUser = Depends(current_user)):
    await service.delete(user.id, resume_id)
    return Response(status_code=204)
