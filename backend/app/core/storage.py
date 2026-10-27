"""S3 相容物件儲存：預簽名上傳／下載、HEAD 驗證、讀取、刪除。

boto3 是同步函式庫，網路呼叫一律丟到 thread（anyio.to_thread）。
預簽名網址用 s3_public_endpoint（瀏覽器看得到的位址）簽發。
"""
import anyio
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.core.config import settings

_cfg = Config(signature_version="s3v4", s3={"addressing_style": "path"})


def _make(endpoint: str):
    return boto3.client("s3", endpoint_url=endpoint, region_name=settings.s3_region,
                        aws_access_key_id=settings.s3_access_key, aws_secret_access_key=settings.s3_secret_key,
                        config=_cfg)


_s3 = _make(settings.s3_endpoint)
_s3_public = _make(settings.s3_public_endpoint or settings.s3_endpoint)
B = settings.s3_bucket


def presign_put(key: str, content_type: str, expires: int = 300) -> str:
    return _s3_public.generate_presigned_url(
        "put_object", Params={"Bucket": B, "Key": key, "ContentType": content_type}, ExpiresIn=expires)


def presign_get(key: str, expires: int = 300, content_type: str | None = None) -> str:
    params = {"Bucket": B, "Key": key}
    if content_type:
        params["ResponseContentType"] = content_type
    return _s3_public.generate_presigned_url("get_object", Params=params, ExpiresIn=expires)


async def head(key: str) -> dict | None:
    def _h():
        try:
            return _s3.head_object(Bucket=B, Key=key)
        except ClientError:
            return None
    return await anyio.to_thread.run_sync(_h)


async def read(key: str, byte_range: str | None = None) -> bytes:
    def _r():
        kw = {"Bucket": B, "Key": key}
        if byte_range:
            kw["Range"] = byte_range
        return _s3.get_object(**kw)["Body"].read()
    return await anyio.to_thread.run_sync(_r)


async def put(key: str, data: bytes, content_type: str) -> None:
    await anyio.to_thread.run_sync(lambda: _s3.put_object(Bucket=B, Key=key, Body=data, ContentType=content_type))


async def copy(src: str, dst: str) -> None:
    await anyio.to_thread.run_sync(lambda: _s3.copy_object(Bucket=B, Key=dst, CopySource={"Bucket": B, "Key": src}))


async def delete(*keys: str) -> None:
    keys = tuple(k for k in keys if k)
    if not keys:
        return
    await anyio.to_thread.run_sync(
        lambda: _s3.delete_objects(Bucket=B, Delete={"Objects": [{"Key": k} for k in keys], "Quiet": True}))


async def ensure_bucket() -> None:
    def _e():
        try:
            _s3.head_bucket(Bucket=B)
        except ClientError:
            _s3.create_bucket(Bucket=B)
        # 允許瀏覽器直接 PUT／GET（預簽名網址）
        try:
            _s3.put_bucket_cors(Bucket=B, CORSConfiguration={"CORSRules": [{
                "AllowedOrigins": [settings.base_url, "http://localhost:8009", "http://127.0.0.1:8009"],
                "AllowedMethods": ["PUT", "GET", "HEAD"], "AllowedHeaders": ["*"], "MaxAgeSeconds": 3000}]})
        except ClientError:
            pass  # MinIO 預設允許所有來源
    await anyio.to_thread.run_sync(_e)
