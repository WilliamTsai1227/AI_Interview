"""直接上傳（architecture.md §6.9）：發上傳單據與預簽名 PUT；使用時以 HEAD＋magic bytes 驗證。"""
import uuid
from uuid import UUID

from app.core import redis, storage
from app.core.config import settings
from app.core.db import Connection, transaction
from app.core.errors import AppError

RESUME_TYPES = {
    "application/pdf": "pdf",
    "application/msword": "doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
}
AUDIO_TYPES = {"audio/webm": "webm", "audio/mp4": "m4a", "audio/ogg": "ogg", "audio/mpeg": "mp3", "audio/wav": "wav"}
MAGIC = {
    "pdf": [b"%PDF"], "docx": [b"PK\x03\x04"], "doc": [b"\xd0\xcf\x11\xe0"],
    "webm": [b"\x1a\x45\xdf\xa3"], "ogg": [b"OggS"], "mp3": [b"ID3", b"\xff\xfb", b"\xff\xf3"], "wav": [b"RIFF"],
}


def norm_type(ct: str) -> str:
    return (ct or "").split(";")[0].strip().lower()


def _upload_error() -> AppError:
    return AppError(409, "UPLOAD_NOT_FOUND", "找不到上傳的檔案，請重新上傳")


async def create_ticket(user_id: UUID, purpose: str, content_type: str, size_bytes: int,
                        filename: str | None, attempt_id: UUID | None) -> dict:
    await redis.rate_limit("upload", str(user_id))
    ct = norm_type(content_type)
    if purpose == "resume":
        if ct not in RESUME_TYPES:
            raise AppError(415, "UNSUPPORTED_MEDIA_TYPE", "只支援 PDF 或 Word 檔")
        max_bytes = settings.resume_max_bytes
    else:
        if ct not in AUDIO_TYPES:
            raise AppError(415, "UNSUPPORTED_MEDIA_TYPE", "錄音格式不支援")
        max_bytes = settings.audio_max_bytes
    if size_bytes > max_bytes:
        raise AppError(413, "FILE_TOO_LARGE", f"檔案超過 {max_bytes // 1024 // 1024} MB", {"max_bytes": max_bytes})
    tid = uuid.uuid4()
    key = f"users/{user_id}/uploads/{tid}"
    async with transaction() as conn:
        if purpose == "answer_audio":
            ok = await conn.fetchval(
                """
                SELECT 1 FROM answer_attempts a JOIN interview_sessions s ON s.id = a.session_id
                 WHERE a.id = $1 AND s.user_id = $2 AND a.status = 'answering'
                """, attempt_id, user_id)
            if not ok:
                raise AppError(409, "STATE_CONFLICT", "這次作答已經結束，請重新整理")
        await conn.execute(
            """
            INSERT INTO upload_tickets (id, user_id, purpose, object_key, content_type, max_bytes, attempt_id, expires_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, now() + interval '1 hour')
            """, tid, user_id, purpose, key, ct, max_bytes, attempt_id)
    # 檔名放在 metadata 不方便簽名，存在 redis 供 /resumes 使用
    if filename:
        try:
            await redis.client().set(f"{redis.P}upload_name:{tid}", filename[:200], ex=3600)
        except Exception:
            pass
    return {"upload_id": str(tid), "method": "PUT", "url": storage.presign_put(key, ct),
            "headers": {"Content-Type": ct}, "object_key": key, "expires_in": 300}


async def filename_of(upload_id: UUID) -> str | None:
    try:
        return await redis.client().get(f"{redis.P}upload_name:{upload_id}")
    except Exception:
        return None


async def consume(conn: Connection, user_id: UUID, upload_id: UUID, purpose: str,
                  attempt_id: UUID | None = None) -> dict:
    """驗證並標記單據已使用；回傳 {object_key, content_type, size, ext}。"""
    t = await conn.fetchrow(
        "SELECT * FROM upload_tickets WHERE id = $1 AND user_id = $2 AND purpose = $3 FOR UPDATE",
        upload_id, user_id, purpose)
    if not t or t["status"] == "expired":
        raise _upload_error()
    if purpose == "answer_audio" and t["attempt_id"] != attempt_id:
        raise _upload_error()
    h = await storage.head(t["object_key"])
    if not h:
        raise _upload_error()
    size = int(h.get("ContentLength", 0))
    if size <= 0 or size > t["max_bytes"]:
        raise AppError(413, "FILE_TOO_LARGE", "檔案大小不符合上限")
    types = RESUME_TYPES if purpose == "resume" else AUDIO_TYPES
    ext = types.get(t["content_type"], "bin")
    head = await storage.read(t["object_key"], "bytes=0-15")
    sigs = MAGIC.get(ext)
    if ext == "m4a":
        if head[4:8] != b"ftyp":
            raise AppError(415, "UNSUPPORTED_MEDIA_TYPE", "檔案內容和格式不符")
    elif sigs and not any(head.startswith(s) for s in sigs):
        raise AppError(415, "UNSUPPORTED_MEDIA_TYPE", "檔案內容和格式不符")
    if t["status"] != "consumed":
        await conn.execute("UPDATE upload_tickets SET status = 'consumed', consumed_at = now() WHERE id = $1", upload_id)
    return {"object_key": t["object_key"], "content_type": t["content_type"], "size": size, "ext": ext,
            "already_consumed": t["status"] == "consumed"}
