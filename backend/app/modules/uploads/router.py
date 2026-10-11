from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.core.errors import bad_request
from app.core.security import CurrentUser, current_user
from app.modules.uploads import service

router = APIRouter(prefix="/uploads", tags=["uploads"])


class UploadIn(BaseModel):
    purpose: Literal["resume", "answer_audio"]
    content_type: str = Field(max_length=120)
    size_bytes: int = Field(gt=0)
    filename: str | None = Field(default=None, max_length=200)
    attempt_id: UUID | None = None


@router.post("", status_code=201)
async def create_upload(body: UploadIn, user: CurrentUser = Depends(current_user)):
    if body.purpose == "answer_audio" and not body.attempt_id:
        raise bad_request("缺少 attempt_id", {"attempt_id": "required"})
    return await service.create_ticket(user.id, body.purpose, body.content_type, body.size_bytes,
                                       body.filename, body.attempt_id)
