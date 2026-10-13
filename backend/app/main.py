"""FastAPI app：API 掛在 /api/v1，前端靜態檔掛在 /（同網域，Cookie 最省事）。"""
import asyncio
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.core import db, errors, redis, storage
from app.core.config import settings
from app.modules.account.router import router as account
from app.modules.admin.router import router as admin
from app.modules.auth.router import router as auth
from app.modules.interviews.router import router as interviews
from app.modules.jobs.router import router as jobs
from app.modules.preps.router import router as preps
from app.modules.questions.router import router as questions
from app.modules.reports.router import router as reports
from app.modules.resumes.router import router as resumes
from app.modules.targets.router import router as targets
from app.modules.uploads.router import router as uploads
from app.modules.voice.router import router as voice
from app.workers import queue

structlog.configure(processors=[structlog.processors.add_log_level, structlog.processors.TimeStamper(fmt="iso"),
                                structlog.processors.JSONRenderer(ensure_ascii=False)])
log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_pool()
    for i in range(15):             # 物件儲存可能比 API 晚一點就緒（docker compose 同時啟動）
        try:
            await storage.ensure_bucket()
            break
        except Exception as e:
            if i == 14:
                log.warning("bucket_check_failed", error=str(e))
            await asyncio.sleep(1)
    yield
    await queue.close()
    await redis.close()
    await db.close_pool()


app = FastAPI(title="AI Interview API", version="0.4", lifespan=lifespan, docs_url="/api/v1/docs",
              openapi_url="/api/v1/openapi.json", redoc_url=None)
errors.install(app)


@app.middleware("http")
async def request_context(request: Request, call_next):
    rid = request.headers.get("x-request-id") or f"req_{uuid.uuid4().hex[:16]}"
    request.state.request_id = rid
    t0 = time.monotonic()
    response = await call_next(request)
    response.headers["X-Request-ID"] = rid
    if request.url.path.startswith("/api/"):
        log.info("request", method=request.method, path=request.url.path, status=response.status_code,
                 ms=int((time.monotonic() - t0) * 1000), request_id=rid,
                 user_id=str(getattr(request.state, "user_id", "") or ""))
    return response


for r in (auth, account, uploads, resumes, targets, questions, preps, jobs, interviews, reports, voice, admin):
    app.include_router(r, prefix="/api/v1")


@app.get("/api/v1/health")
async def health():
    async with db.connection() as conn:
        await conn.fetchval("SELECT 1")
    return {"ok": True, "ai": "openai" if settings.ai_enabled else "fake"}


FRONTEND = (Path(__file__).resolve().parents[1] / settings.frontend_dir).resolve()
if FRONTEND.exists():
    app.mount("/css", StaticFiles(directory=FRONTEND / "css"), name="css")
    app.mount("/js", StaticFiles(directory=FRONTEND / "js"), name="js")

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(FRONTEND / "index.html", headers={"Cache-Control": "no-cache"})
