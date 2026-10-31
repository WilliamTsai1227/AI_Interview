from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field

from app.core.security import CurrentUser, current_user
from app.core.types import Line, Tag
from app.modules.questions import service

router = APIRouter(tags=["questions"])
Mode = Literal["none", "fixed", "planned", "adaptive"]


class GenerateIn(BaseModel):
    note: str = Field(default="", max_length=300)
    categories: list[Tag] = Field(default_factory=list, max_length=6)
    difficulty: Literal["basic", "medium", "advanced"] = "medium"
    count: int = Field(default=5, ge=3, le=10)


class FollowupIn(BaseModel):
    mode: Mode
    count: int | None = None
    planned: list[Line] | None = Field(default=None, max_length=10)


class ItemIn(BaseModel):
    text: str = Field(max_length=500)
    category: str = Field(default="自訂", max_length=20)
    difficulty: Literal["basic", "medium", "advanced"] = "medium"
    followup: FollowupIn | None = None


class ItemPatch(BaseModel):
    text: str | None = Field(default=None, max_length=500)
    category: str | None = Field(default=None, max_length=20)
    difficulty: Literal["basic", "medium", "advanced"] | None = None


class OrderIn(BaseModel):
    item_ids: list[UUID] = Field(max_length=500)


@router.get("/targets/{target_id}/questions")
async def list_(target_id: UUID, user: CurrentUser = Depends(current_user)):
    return await service.list_(user.id, target_id)


@router.post("/targets/{target_id}/questions/generate", status_code=202)
async def generate(target_id: UUID, body: GenerateIn, user: CurrentUser = Depends(current_user)):
    return await service.generate(user.id, target_id, body.note, body.categories, body.difficulty, body.count)


@router.get("/question-generations/{gid}")
async def generation(gid: UUID, user: CurrentUser = Depends(current_user)):
    return await service.generation(user.id, gid)


@router.post("/targets/{target_id}/questions", status_code=201)
async def add(target_id: UUID, body: ItemIn, user: CurrentUser = Depends(current_user)):
    return await service.add(user.id, target_id, body.text, body.category, body.difficulty,
                             body.followup.model_dump() if body.followup else None)


@router.put("/targets/{target_id}/questions/order")
async def reorder(target_id: UUID, body: OrderIn, user: CurrentUser = Depends(current_user)):
    return await service.reorder(user.id, target_id, body.item_ids)


@router.patch("/targets/{target_id}/questions/{item_id}")
async def patch(target_id: UUID, item_id: UUID, body: ItemPatch, user: CurrentUser = Depends(current_user)):
    return await service.patch(user.id, target_id, item_id, body.text, body.category, body.difficulty)


@router.put("/targets/{target_id}/questions/{item_id}/followups")
async def followups(target_id: UUID, item_id: UUID, body: FollowupIn, user: CurrentUser = Depends(current_user)):
    return await service.set_followups(user.id, target_id, item_id, body.mode, body.count, body.planned)


@router.delete("/targets/{target_id}/questions/{item_id}", status_code=204)
async def delete(target_id: UUID, item_id: UUID, user: CurrentUser = Depends(current_user)):
    await service.delete(user.id, target_id, item_id)
    return Response(status_code=204)
