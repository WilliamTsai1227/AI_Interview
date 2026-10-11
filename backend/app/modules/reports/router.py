import asyncio
import json
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.core import redis
from app.core.security import CurrentUser, current_user
from app.modules.reports import service

router = APIRouter(prefix="/interviews", tags=["reports"])


class AddToSetIn(BaseModel):
    session_question_ids: list[UUID] | None = None


@router.get("")
async def list_(q: str | None = None, limit: int = Query(20, ge=1, le=50), cursor: str | None = None,
                user: CurrentUser = Depends(current_user)):
    return await service.list_(user.id, (q or "").strip() or None, limit, int(cursor) if cursor and cursor.isdigit() else 0)


@router.get("/{sid}/report")
async def report(sid: UUID, user: CurrentUser = Depends(current_user)):
    return await service.detail(user.id, sid)


@router.get("/{sid}/report/events")
async def report_events(sid: UUID, user: CurrentUser = Depends(current_user)):
    """SSE：先送 snapshot，再轉送 Redis Pub/Sub 的評分事件；最長 120 秒。"""
    first = await service.detail(user.id, sid)

    async def stream():
        yield f"event: snapshot\ndata: {json.dumps({**first['progress'], 'status': first['status']})}\n\n"
        if first["status"] == "ready":
            yield f"event: report_ready\ndata: {json.dumps({'overall_score': first['overall_score']})}\n\n"
            return
        ps = redis.client().pubsub()
        try:
            await ps.subscribe(f"{redis.P}evt:report:{sid}")
            loop = asyncio.get_running_loop()
            deadline = loop.time() + 120
            while loop.time() < deadline:
                msg = await ps.get_message(ignore_subscribe_messages=True, timeout=10)
                if msg is None:
                    yield ": ping\n\n"
                    continue
                ev = json.loads(msg["data"])
                yield f"event: {ev['event']}\ndata: {json.dumps(ev['data'], default=str)}\n\n"
                if ev["event"] == "report_ready":
                    return
        finally:
            await ps.unsubscribe()
            await ps.aclose()

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/{sid}/answers/{aid}/audio")
async def audio(sid: UUID, aid: UUID, user: CurrentUser = Depends(current_user)):
    return await service.audio_url(user.id, sid, aid)


@router.post("/{sid}/report/add-to-set")
async def add_to_set(sid: UUID, body: AddToSetIn | None = None, user: CurrentUser = Depends(current_user)):
    return await service.add_to_set(user.id, sid, body.session_question_ids if body else None)


@router.post("/{sid}/questions/{qid}/re-evaluate", status_code=202)
async def re_evaluate(sid: UUID, qid: UUID, user: CurrentUser = Depends(current_user)):
    return await service.re_evaluate(user.id, sid, qid)


@router.delete("/{sid}", status_code=204)
async def delete(sid: UUID, user: CurrentUser = Depends(current_user)):
    await service.delete(user.id, sid)
    return Response(status_code=204)
