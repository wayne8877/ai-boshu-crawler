"""平台爬虫抽象基类。

每个平台（bilibili / douyin / youtube / ...）实现一个 PlatformCrawler 子类。
平台实现注册到 PLATFORM_REGISTRY，pipeline 按 platform name 查找。
"""
from __future__ import annotations

import abc
from pathlib import Path
from typing import Any, ClassVar

from ..core.config import CreatorTrackerConfig
from ..core.logging import get_logger
from ..core.models import Creator, Platform, Video, VideoComment

logger = get_logger(__name__)


class PlatformCrawler(abc.ABC):
    """单个平台爬虫的抽象基类。

    子类必须实现：
        - platform: ClassVar[Platform]
        - list_videos(creator, limit) -> list[Video]  拉取最新视频（不下载）
        - download_video(video, output_dir) -> Path  下载视频文件

    可选重写：
        - load_creators() / save_creators()  从飞书 Base 或本地加载博主列表
        - list_comments(video, limit) -> list[VideoComment]  评论抓取（部分平台无）
        - enrich_video(video) -> Video  补充字段（封面、tag 等）
    """

    platform: ClassVar[Platform]

    def __init__(self, config: CreatorTrackerConfig) -> None:
        self.config = config
        self.log = get_logger(f"{__name__}.{self.platform.value}")

    @abc.abstractmethod
    async def list_videos(self, creator: Creator, limit: int) -> list[Video]:
        """拉取该博主最新 limit 个视频（不下载）。"""
        raise NotImplementedError

    @abc.abstractmethod
    async def download_video(self, video: Video, output_dir: Path) -> Path:
        """下载视频到 output_dir，返回本地绝对路径。"""
        raise NotImplementedError

    async def list_comments(self, video: Video, limit: int = 50) -> list[VideoComment]:
        """抓取视频 top-N 评论。

        默认返回空列表（平台无评论能力或未实现）。
        """
        self.log.debug("list_comments_not_implemented", platform=self.platform, video_id=video.video_id)
        return []

    async def enrich_video(self, video: Video) -> Video:
        """补充视频字段（封面 URL / 标签 / 准确发布时间）。

        默认 no-op。
        """
        return video

    async def health_check(self) -> dict[str, Any]:
        """平台连通性自检（登录态 / API 限流 / 凭证有效期）。

        默认返回 {"ok": True}。子类覆盖时跑一次最小请求。
        """
        return {"ok": True, "platform": self.platform.value}


# 全局平台注册表（platform -> PlatformCrawler 类）
PLATFORM_REGISTRY: dict[Platform, type[PlatformCrawler]] = {}


def register_platform(cls: type[PlatformCrawler]) -> type[PlatformCrawler]:
    """装饰器：把平台实现注册到 PLATFORM_REGISTRY。"""
    if not hasattr(cls, "platform"):
        raise TypeError(f"{cls.__name__} 必须声明 ClassVar platform")
    PLATFORM_REGISTRY[cls.platform] = cls
    return cls


def get_crawler(platform: Platform, config: CreatorTrackerConfig) -> PlatformCrawler:
    """按 platform 拿到对应 crawler 实例（找不到抛 PlatformNotSupportedError）。"""
    from ..core.exceptions import PlatformNotSupportedError

    cls = PLATFORM_REGISTRY.get(platform)
    if cls is None:
        raise PlatformNotSupportedError(
            f"平台 {platform.value} 未实现或未注册。当前已注册: {sorted(p.value for p in PLATFORM_REGISTRY)}",
            platform=platform.value,
        )
    return cls(config)
