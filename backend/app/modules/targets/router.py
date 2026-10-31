from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.security import CurrentUser, current_user
from app.modules.targets import service

router = APIRouter(prefix="/targets", tags=["targets"])


def _check_url(v: str | None) -> str | None:
    """職缺網址（選填）：只接受 http(s)，避免 javascript: 之類的連結被放進 href。空字串＝清除。"""
    if v is None:
        return None
    v = v.strip()
    if not v:
        return ""
    u = urlsplit(v)
    if u.scheme not in ("http", "https") or not u.netloc:
        raise ValueError("職缺網址要以 http:// 或 https:// 開頭")
    return v


class TargetIn(BaseModel):
    raw_text: str | None = Field(default=None, max_length=20000)
    job_post_id: UUID | None = None
    resume_id: UUID | None = None
    source_url: str | None = Field(default=None, max_length=1000)

    _url = field_validator("source_url")(_check_url)

    @model_validator(mode="after")
    def one_source(self):
        if not self.raw_text and not self.job_post_id:
            raise ValueError("需要 raw_text 或 job_post_id")
        return self


class JobFix(BaseModel):
    title: str | None = Field(default=None, max_length=120)
    company_name: str | None = Field(default=None, max_length=120)
    location_text: str | None = Field(default=None, max_length=120)
    salary_text: str | None = Field(default=None, max_length=120)
    about: str | None = Field(default=None, max_length=2000)
    duties: list[str] | None = None
    requirements: list[str] | None = None


class TargetPatch(BaseModel):
    resume_id: UUID | None = None
    clear_resume: bool = False
    job: JobFix | None = None
    raw_text: str | None = Field(default=None, max_length=20000)     # 只存文字，不會重新出題
    source_url: str | None = Field(default=None, max_length=1000)    # 空字串＝清除；不會重新出題

    _url = field_validator("source_url")(_check_url)


@router.get("")
async def list_(user: CurrentUser = Depends(current_user)):
    return {"items": await service.list_(user.id)}


@router.post("")
async def create(body: TargetIn, response: Response, user: CurrentUser = Depends(current_user)):
    status, data = await service.create(user.id, body.raw_text, body.job_post_id, body.resume_id, body.source_url)
    response.status_code = status
    return data


@router.get("/{target_id}")
async def get(target_id: UUID, user: CurrentUser = Depends(current_user)):
    return await service.get(user.id, target_id)


@router.patch("/{target_id}")
async def patch(target_id: UUID, body: TargetPatch, user: CurrentUser = Depends(current_user)):
    return await service.patch(user.id, target_id, body.resume_id, body.clear_resume,
                               body.job.model_dump() if body.job else None, body.raw_text, body.source_url)


@router.post("/{target_id}/regenerate", status_code=202)
async def regenerate(target_id: UUID, user: CurrentUser = Depends(current_user)):
    return await service.regenerate(user.id, target_id)


@router.delete("/{target_id}", status_code=204)
async def delete(target_id: UUID, user: CurrentUser = Depends(current_user)):
    await service.archive(user.id, target_id)
    return Response(status_code=204)
