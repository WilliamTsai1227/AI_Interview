from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Response
from pydantic import BaseModel, Field

from app.core.errors import bad_request
from app.core.security import CurrentUser, current_user
from app.modules.interviews import service

router = APIRouter(prefix="/interviews", tags=["interviews"])


class CreateIn(BaseModel):
    target_id: UUID
    resume_id: UUID | None = None
    length_mode: Literal["quick", "standard", "deep"] = "standard"
    persona: Literal["warm", "real", "tough"] = "warm"
    language: Literal["zh", "en", "mixed"] = "zh"
    recording_consent: bool = False


class Versioned(BaseModel):
    expected_version: int | None = None


class PlaybackIn(Versioned):
    spoken_text: str | None = Field(default=None, max_length=500)
    fallback: str | None = Field(default=None, max_length=40)


class CompleteIn(Versioned):
    upload_id: UUID
    client_started_at: str | None = None
    client_ended_at: str | None = None
    client_duration_ms: int | None = Field(default=None, ge=0, le=3_600_000)
    end_trigger: Literal["user_click", "max_duration", "page_hide"] = "user_click"
    client_transcript: str | None = Field(default=None, max_length=8000)   # 開發模式：瀏覽器語音辨識的結果


class PauseIn(Versioned):
    reason: Literal["user", "page_leave"] = "user"


class EndIn(Versioned):
    reason: Literal["user_ended", "completed"] = "user_ended"


class HeartbeatIn(BaseModel):
    phase: str | None = Field(default=None, max_length=40)
    rtc_state: str | None = Field(default=None, max_length=40)
    events: list[dict] = Field(default_factory=list, max_length=50)


@router.post("", status_code=201)
async def create(body: CreateIn, user: CurrentUser = Depends(current_user)):
    return await service.create(user.id, body.target_id, body.resume_id, "resume_id" in body.model_fields_set,
                                body.length_mode, body.persona, body.language, body.recording_consent)


@router.get("/active")
async def active(user: CurrentUser = Depends(current_user)):
    s = await service.get_active(user.id)
    return s if s else Response(status_code=204)


@router.get("/{sid}")
async def get(sid: UUID, user: CurrentUser = Depends(current_user)):
    return await service.get(user.id, sid)


@router.post("/{sid}/start")
async def start(sid: UUID, body: Versioned, user: CurrentUser = Depends(current_user)):
    return await service.start(user.id, sid, body.expected_version)


@router.post("/{sid}/questions/{qid}/playback-finished")
async def playback_finished(sid: UUID, qid: UUID, body: PlaybackIn, user: CurrentUser = Depends(current_user)):
    return {"session": await service.playback_finished(user.id, sid, qid, body.expected_version, body.spoken_text, body.fallback)}


@router.post("/{sid}/questions/{qid}/answers", status_code=201)
async def begin_answer(sid: UUID, qid: UUID, body: Versioned, user: CurrentUser = Depends(current_user)):
    return await service.begin_answer(user.id, sid, qid, body.expected_version)


@router.post("/{sid}/attempts/{aid}/complete")
async def complete(sid: UUID, aid: UUID, body: CompleteIn, user: CurrentUser = Depends(current_user),
                   idempotency_key: str | None = Header(default=None)):
    if not idempotency_key or len(idempotency_key) > 100:
        raise bad_request("缺少 Idempotency-Key", {"Idempotency-Key": "required"})
    return await service.complete(user.id, sid, aid, body.model_dump(mode="json"), idempotency_key)


@router.post("/{sid}/attempts/{aid}/discard")
async def discard(sid: UUID, aid: UUID, body: Versioned, user: CurrentUser = Depends(current_user)):
    return {"session": await service.discard(user.id, sid, aid, body.expected_version)}


@router.post("/{sid}/questions/{qid}/skip")
async def skip(sid: UUID, qid: UUID, body: Versioned, user: CurrentUser = Depends(current_user)):
    return await service.skip(user.id, sid, qid, body.expected_version)


@router.post("/{sid}/pause")
async def pause(sid: UUID, body: PauseIn, user: CurrentUser = Depends(current_user)):
    return await service.pause(user.id, sid, body.expected_version, body.reason)


@router.post("/{sid}/resume")
async def resume(sid: UUID, body: Versioned, user: CurrentUser = Depends(current_user)):
    return await service.resume(user.id, sid, body.expected_version)


@router.post("/{sid}/heartbeat", status_code=204)
async def heartbeat(sid: UUID, body: HeartbeatIn, user: CurrentUser = Depends(current_user)):
    await service.heartbeat(user.id, sid, body.events)
    return Response(status_code=204)


@router.post("/{sid}/end")
async def end(sid: UUID, body: EndIn, user: CurrentUser = Depends(current_user)):
    return await service.end(user.id, sid, body.expected_version, body.reason)


@router.post("/{sid}/realtime/connect")
async def realtime_connect(sid: UUID, user: CurrentUser = Depends(current_user)):
    from app.core.errors import AppError
    raise AppError(503, "REALTIME_UNAVAILABLE", "改用標準語音")   # P3：Realtime 尚未啟用，前端自動改用 scripted
