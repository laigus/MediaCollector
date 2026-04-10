from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.models import MediaItem, TargetInfo


class BaseProvider(ABC):
    """所有平台 Provider 的抽象基类。

    新增平台只需继承此类并实现三个抽象方法，
    然后放到 providers/ 目录下即可自动注册。
    """

    name: str = ""
    display_name: str = ""
    description: str = ""

    @abstractmethod
    async def parse_target(self, url_or_id: str) -> TargetInfo:
        """解析目标 URL 或 ID，返回结构化的目标信息。"""

    @abstractmethod
    async def fetch_media_list(
        self,
        target: TargetInfo,
        *,
        start_page: int = 1,
        end_page: int = 0,
        downloaded_ids: set[str] | None = None,
        progress_callback: Any = None,
    ) -> list[MediaItem]:
        """获取目标下的所有媒体项列表。

        Args:
            target: 解析后的目标信息
            start_page: 起始页码（从 1 开始）
            end_page: 结束页码（0 表示不限制，下载到最后一页）
            downloaded_ids: 已下载的 source_id 集合（保留用于去重）
            progress_callback: 可选的进度回调 (fetched_count, message)
        """

    @abstractmethod
    def get_config_schema(self) -> dict[str, Any]:
        """返回该 Provider 需要的配置项 JSON Schema。

        示例返回:
        {
            "cookie": {"type": "string", "title": "Cookie", "description": "..."}
        }
        """

    def get_download_headers(self) -> dict[str, str] | None:
        """返回下载媒体文件时需要的额外 HTTP headers（如 Referer、Cookie）。"""
        return None

    async def validate_config(self, config: dict[str, Any]) -> bool:
        """验证配置是否有效，默认返回 True。"""
        return True

    async def close(self) -> None:
        """清理资源，默认空实现。"""
