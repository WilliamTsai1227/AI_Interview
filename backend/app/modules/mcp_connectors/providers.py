"""支援的 MCP provider。新增一家只要在 PROVIDERS 加一筆（以及 migration 的 CHECK）。"""
from dataclasses import dataclass

from app.core.config import settings


@dataclass(frozen=True)
class Provider:
    key: str
    name: str             # 顯示名稱
    server_url: str       # MCP server（Streamable HTTP）


def _providers() -> dict[str, Provider]:
    return {"notion": Provider("notion", "Notion", settings.notion_mcp_url)}


def get(key: str) -> Provider | None:
    return _providers().get(key)


def all_() -> list[Provider]:
    return list(_providers().values())
