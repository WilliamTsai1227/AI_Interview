from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field

from app.core.security import CurrentUser, current_user
from app.modules.preps import service

router = APIRouter(tags=["preps"])


class AnalyzeIn(BaseModel):
    resume_id: UUID | None = None


class LikelyMoreIn(BaseModel):
    note: str = Field(default="", max_length=300)
    category: str | None = Field(default=None, max_length=20)
    count: Literal[3, 5, 8] = 3


class CheckMoreIn(BaseModel):
    note: str = Field(default="", max_length=300)
    count: Literal[3, 5, 8] = 3


class CheckIn(BaseModel):
    is_checked: bool


class AddToSetIn(BaseModel):
    ids: list[UUID] | None = None


@router.get("/targets/{target_id}/prep")
async def get(target_id: UUID, user: CurrentUser = Depends(current_user)):
    return await service.get(user.id, target_id)


@router.post("/targets/{target_id}/prep/analyze", status_code=202)
async def analyze(target_id: UUID, body: AnalyzeIn | None = None, user: CurrentUser = Depends(current_user)):
    return await service.analyze(user.id, target_id, body.resume_id if body else None)


@router.post("/preps/{prep_id}/likely-questions/generate", status_code=202)
async def likely_more(prep_id: UUID, body: LikelyMoreIn, user: CurrentUser = Depends(current_user)):
    return await service.more(user.id, prep_id, "likely_more", body.note, body.category, body.count)


@router.post("/preps/{prep_id}/checklist/generate", status_code=202)
async def checklist_more(prep_id: UUID, body: CheckMoreIn, user: CurrentUser = Depends(current_user)):
    return await service.more(user.id, prep_id, "checklist_more", body.note, None, body.count)


@router.get("/prep-generations/{gid}")
async def generation(gid: UUID, user: CurrentUser = Depends(current_user)):
    return await service.generation(user.id, gid)


@router.patch("/preps/{prep_id}/checklist/{item_id}", status_code=204)
async def toggle(prep_id: UUID, item_id: UUID, body: CheckIn, user: CurrentUser = Depends(current_user)):
    await service.toggle(user.id, prep_id, item_id, body.is_checked)
    return Response(status_code=204)


@router.post("/preps/{prep_id}/likely-questions/add-to-set")
async def add_to_set(prep_id: UUID, body: AddToSetIn | None = None, user: CurrentUser = Depends(current_user)):
    return await service.add_to_set(user.id, prep_id, body.ids if body else None)
