"""機密資料加解密（AES-256-GCM）：目前用在 MCP 連接器的 OAuth token。

密文格式：12 bytes nonce + ciphertext（含 16 bytes tag）。金鑰版本另外存在資料表的 key_version，
日後換金鑰時新舊版本並存、逐筆重新加密。
金鑰：MCP_CONNECTOR_ENC_KEY（32 bytes 的 base64，產生方式：openssl rand -base64 32）。
開發環境沒設定時由 JWT_SECRET 推導，方便本機直接跑；正式環境沒設定 → 連接器整個關閉。
"""
import base64
import hashlib
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import settings

KEY_VERSION = 1


def _key() -> bytes:
    if settings.mcp_connector_enc_key:
        key = base64.b64decode(settings.mcp_connector_enc_key)
        if len(key) != 32:
            raise ValueError("MCP_CONNECTOR_ENC_KEY 必須是 32 bytes 的 base64")
        return key
    if settings.is_dev:
        return hashlib.sha256(f"mcp-connector:{settings.jwt_secret}".encode()).digest()
    raise RuntimeError("沒有設定 MCP_CONNECTOR_ENC_KEY")


def encrypt(plain: str, aad: bytes = b"") -> bytes:
    nonce = os.urandom(12)
    return nonce + AESGCM(_key()).encrypt(nonce, plain.encode(), aad or None)


def decrypt(blob: bytes, aad: bytes = b"") -> str:
    return AESGCM(_key()).decrypt(bytes(blob[:12]), bytes(blob[12:]), aad or None).decode()
