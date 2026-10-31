"""HTTP 層防護：請求內容大小上限、安全標頭。

- 大小上限：API 只收 JSON（檔案是瀏覽器直接上傳到 S3／MinIO），超過 1 MB 一律 413，
  避免有人送超大內容拖垮 JSON 解析與記憶體。沒有 Content-Length（chunked）時邊收邊數。
- 安全標頭：CSP 只允許自己網域的程式；禁止被別的網站用 iframe 嵌入（點擊劫持）；
  禁止瀏覽器猜測檔案類型；不把完整網址外流給其他網站。
"""
import json
from urllib.parse import urlsplit

from app.core.config import settings

MAX_BODY = 1024 * 1024


def _origin(url: str) -> str:
    u = urlsplit(url)
    return f"{u.scheme}://{u.netloc}" if u.scheme and u.netloc else ""


# 履歷與錄音是瀏覽器用預簽名網址直接傳到 S3／MinIO，播放錄音也是，所以要放行那個來源
_S3 = _origin(settings.s3_public_endpoint or settings.s3_endpoint)
CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",          # 前端用 style 屬性排版；樣式無法執行程式
    "img-src 'self' data: blob:",
    f"media-src 'self' blob: {_S3}".strip(),
    f"connect-src 'self' {_S3}".strip(),
    "object-src 'none'",
    "base-uri 'none'",
    "frame-ancestors 'none'",
    "form-action 'self' https://accounts.google.com",
])
HEADERS = [
    (b"content-security-policy", CSP.encode()),
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"strict-origin-when-cross-origin"),
    (b"permissions-policy", b"microphone=(self), camera=(), geolocation=()"),
]


class HttpGuard:
    """純 ASGI middleware（不用 BaseHTTPMiddleware，避免多一層串流包裝）。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                names = {k.lower() for k, _ in message.get("headers", [])}
                message["headers"] = list(message.get("headers", [])) + [(k, v) for k, v in HEADERS if k not in names]
            await send(message)

        declared = dict(scope.get("headers") or []).get(b"content-length")
        if declared is not None and (not declared.isdigit() or int(declared) > MAX_BODY):
            return await _too_large(send_with_headers)

        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > MAX_BODY:
                    raise _BodyTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send_with_headers)
        except _BodyTooLarge:
            await _too_large(send_with_headers)


class _BodyTooLarge(Exception):
    pass


async def _too_large(send) -> None:
    body = json.dumps({"error": {"code": "PAYLOAD_TOO_LARGE", "message": "送出的內容太大了", "details": {}}},
                      ensure_ascii=False).encode()
    await send({"type": "http.response.start", "status": 413,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})
