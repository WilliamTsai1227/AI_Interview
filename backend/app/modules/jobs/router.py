from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.core.security import CurrentUser, current_user
from app.modules.jobs import service

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("")
async def search(q: str | None = None, city: str | None = None, category: str | None = None,
                 seniority: str | None = None, filters: str | None = None, sort: str = "match",
                 limit: int = Query(20, ge=1, le=50), cursor: str | None = None,
                 user: CurrentUser = Depends(current_user)):
    offset = int(cursor) if cursor and cursor.isdigit() else 0
    return await service.search(user.id, q=(q or "").strip() or None, city=city, category=category,
                                seniority=seniority, filters=[f for f in (filters or "").split(",") if f],
                                sort=sort, limit=limit, offset=offset)


@router.get("/{job_id}")
async def get(job_id: UUID, user: CurrentUser = Depends(current_user)):
    return await service.get(user.id, job_id)
