from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field

from app.core.security import CurrentUser, current_user
from app.modules.career import service

router = APIRouter(prefix="/career", tags=["career"])


def _clean(xs: list[str]) -> list[str]:
    return list(dict.fromkeys(x.strip() for x in xs if x.strip()))[:10]


class ProfileIn(BaseModel):
    self_intro: str = Field(default="", max_length=3000)
    experience_note: str = Field(default="", max_length=3000)
    salary_min: int | None = Field(default=None, ge=0, le=100_000_000)
    salary_period: Literal["month", "year"] = "month"
    industries: list[str] = Field(default_factory=list, max_length=10)
    roles: list[str] = Field(default_factory=list, max_length=10)
    duties_pref: str = Field(default="", max_length=1000)
    work_hours: str = Field(default="", max_length=200)
    locations: list[str] = Field(default_factory=list, max_length=10)
    work_modes: list[Literal["onsite", "hybrid", "remote"]] = Field(default_factory=list, max_length=3)


class TaskIn(BaseModel):
    is_checked: bool


@router.get("/profile")
async def get_profile(user: CurrentUser = Depends(current_user)):
    return await service.get_profile(user.id)


@router.put("/profile")
async def put_profile(body: ProfileIn, user: CurrentUser = Depends(current_user)):
    d = body.model_dump()
    d.update(self_intro=body.self_intro.strip(), experience_note=body.experience_note.strip(), duties_pref=body.duties_pref.strip(),
             work_hours=body.work_hours.strip(), industries=_clean(body.industries), roles=_clean(body.roles),
             locations=_clean(body.locations), work_modes=list(dict.fromkeys(body.work_modes)))
    return await service.put_profile(user.id, d)


@router.post("/plans", status_code=202)
async def start(user: CurrentUser = Depends(current_user)):
    return await service.start(user.id)


@router.get("/plans")
async def list_plans(user: CurrentUser = Depends(current_user)):
    return {"items": await service.list_plans(user.id)}


@router.get("/plans/latest")
async def latest(user: CurrentUser = Depends(current_user)):
    return {"plan": await service.get_plan(user.id, None)}


@router.get("/plans/{plan_id}")
async def get_plan(plan_id: UUID, user: CurrentUser = Depends(current_user)):
    return await service.get_plan(user.id, plan_id)


@router.patch("/plans/{plan_id}/tasks/{task_id}", status_code=204)
async def toggle(plan_id: UUID, task_id: UUID, body: TaskIn, user: CurrentUser = Depends(current_user)):
    await service.toggle_task(user.id, plan_id, task_id, body.is_checked)
    return Response(status_code=204)
