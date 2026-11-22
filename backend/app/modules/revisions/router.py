from typing import Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.errors import AppError
from app.core.security import CurrentUser, current_user
from app.core.types import Link
from app.modules.career import github
from app.modules.membership.quota import require_tokens
from app.modules.revisions import service

router = APIRouter(prefix="/resume-revisions", tags=["resume-revisions"])


class RevisionIn(BaseModel):
    resume_id: UUID
    target_ids: list[UUID] = Field(default_factory=list, max_length=10)
    goal: Literal["target", "refresh", "switch", "first"] = "refresh"
    language: Literal["zh", "en"] = "zh"
    length: Literal["one", "two"] = "one"
    correction: str = Field(default="", max_length=500)
    portfolio_links: list[Link] = Field(default_factory=list, max_length=10)     # GitHub repo 網址


class AnswerIn(BaseModel):
    choice: str | None = Field(default=None, max_length=40)
    text: str = Field(default="", max_length=1000)
    skipped: bool = False


class AnswersIn(BaseModel):
    answers: dict[str, AnswerIn] = Field(max_length=20)


class DecisionIn(BaseModel):
    accept: bool = True
    text: str | None = Field(default=None, max_length=1000)


class DecisionsIn(BaseModel):
    decisions: dict[str, DecisionIn] = Field(max_length=300)


class SaveIn(BaseModel):
    set_primary: bool = False


@router.post("", status_code=202)
async def create(body: RevisionIn, user: CurrentUser = Depends(require_tokens)):
    repos = [github.parse(u.strip()) for u in body.portfolio_links if u.strip()]
    if any(not t or not t[1] for t in repos):
        raise AppError(400, "VALIDATION_ERROR", "只能放 GitHub repo 的網址，例如 github.com/帳號/專案", {"fields": {"portfolio_links": "invalid"}})
    links = list(dict.fromkeys(f"https://github.com/{o}/{r}" for o, r in repos))
    if len(links) > settings.portfolio_max_repos:
        raise AppError(400, "VALIDATION_ERROR", f"一次最多讀 {settings.portfolio_max_repos} 個 repo", {"fields": {"portfolio_links": "count"}})
    setup = {"goal": body.goal, "language": body.language, "length": body.length, "correction": body.correction.strip(),
             "portfolio_links": links}
    return await service.create(user.id, body.resume_id, body.target_ids, setup)


@router.get("")
async def list_(user: CurrentUser = Depends(current_user)):
    return {"items": await service.list_(user.id)}


@router.get("/github-repos")         # 要放在 /{rid} 前面，否則會被當成 id
async def github_repos(owner: str = Query(min_length=1, max_length=200), user: CurrentUser = Depends(current_user)):
    return await service.github_repos(user.id, owner)


@router.get("/{rid}")
async def get(rid: UUID, user: CurrentUser = Depends(current_user)):
    return await service.get(user.id, rid)


@router.put("/{rid}/answers")
async def put_answers(rid: UUID, body: AnswersIn, user: CurrentUser = Depends(current_user)):
    return await service.put_answers(user.id, rid, {k: v.model_dump() for k, v in body.answers.items()})


@router.post("/{rid}/generate", status_code=202)
async def generate(rid: UUID, user: CurrentUser = Depends(require_tokens)):
    return await service.generate(user.id, rid)


@router.put("/{rid}/decisions")
async def put_decisions(rid: UUID, body: DecisionsIn, user: CurrentUser = Depends(current_user)):
    return await service.put_decisions(user.id, rid, {k: v.model_dump() for k, v in body.decisions.items()})


@router.get("/{rid}/docx")
async def docx(rid: UUID, user: CurrentUser = Depends(current_user)):
    data, name = await service.docx_file(user.id, rid)
    return Response(data, media_type=service.DOCX, headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}", "Cache-Control": "private, no-store"})


@router.post("/{rid}/save")
async def save(rid: UUID, body: SaveIn, user: CurrentUser = Depends(current_user)):
    return await service.save(user.id, rid, body.set_primary)
