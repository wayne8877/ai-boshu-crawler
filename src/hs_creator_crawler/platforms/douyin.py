"""Douyin（抖音）平台实现。

爬虫主路径靠 yt-dlp（支持抖音视频直链）。登录态 / 用户主页爬取后续
从 dragon-hh 原仓 `download_douyin_latest.py` 迁移 CDP/CDP-bridge 逻辑。

当前实现保证接口完整，主路径端到端可跑（基于 yt-dlp + sec_uid URL）。
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar

import yt_dlp

from ..core.exceptions import DownloadError, PlatformError
from ..core.logging import get_logger
from ..core.models import Creator, Platform, Video
from .base import PlatformCrawler, register_platform

logger = get_logger(__name__)


@register_platform
class DouyinCrawler(PlatformCrawler):
    """抖音数据采集（基于 yt-dlp，sec_uid URL 模式）。"""

    platform: ClassVar[Platform] = Platform.DOUYIN

    YDL_OPTS: ClassVar[dict[str, Any]] = {
        "quiet": True,
        "no_warnings": True,
        "format": "bv*+ba/best",
        "merge_output_format": "mp4",
        "outtmpl": "%(id)s.%(ext)s",
    }

    async def list_videos(self, creator: Creator, limit: int) -> list[Video]:
        """从抖音用户主页拉取最新视频（yt-dlp 模式）。

        注意：抖音反爬严格，必须带正确的 Cookie 或 sec_uid URL 才能拿到列表。
        后续从原仓迁移 CDP 桥接逻辑（fetch_douyin_comments_cdp.mjs）。
        """
        url = str(creator.url)
        opts: dict[str, Any] = {**self.YDL_OPTS, "extract_flat": True, "playlistend": limit, "skip_download": True}

        def _extract() -> list[Video]:
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=False)
            except Exception as exc:
                raise PlatformError(str(exc), platform=self.platform.value) from exc
            entries = (info or {}).get("entries") or []
            videos: list[Video] = []
            for entry in entries[:limit]:
                entry_dict = dict(entry) if entry else {}
                video_id = entry_dict.get("id")
                if not video_id:
                    continue
                videos.append(
                    Video(
                        platform=self.platform,
                        video_id=str(video_id),
                        creator_id=creator.creator_id,
                        title=entry_dict.get("title") or "",
                        description=entry_dict.get("description") or "",
                        url=entry_dict.get("url") or f"https://www.douyin.com/video/{video_id}",
                        published_at=datetime.fromtimestamp(entry_dict.get("timestamp") or 0) if entry_dict.get("timestamp") else datetime.now(),
                        duration_seconds=int(entry_dict.get("duration") or 0),
                        view_count=int(entry_dict.get("view_count") or 0),
                        like_count=int(entry_dict.get("like_count") or 0),
                    )
                )
            return videos

        videos = await asyncio.to_thread(_extract)
        self.log.info("list_videos_done", creator=creator.name, count=len(videos))
        return videos

    async def download_video(self, video: Video, output_dir: Path) -> Path:
        """下载视频到 output_dir。"""
        output_dir.mkdir(parents=True, exist_ok=True)

        def _do_download() -> Path:
            opts = {**self.YDL_OPTS, "outtmpl": str(output_dir / "%(id)s.%(ext)s")}
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    ydl.download([str(video.url)])
            except Exception as exc:
                raise DownloadError(str(exc), platform=self.platform.value, video_id=video.video_id) from exc
            candidates = sorted(output_dir.glob(f"{video.video_id}.*"))
            for ext in (".mp4", ".mkv"):
                matches = [c for c in candidates if c.suffix == ext]
                if matches:
                    return matches[0]
            raise DownloadError(
                f"下载未产出 {video.video_id} 文件于 {output_dir}",
                platform=self.platform.value,
                video_id=video.video_id,
            )

        path = await asyncio.to_thread(_do_download)
        self.log.info("download_done", video_id=video.video_id, path=str(path))
        return path

    async def health_check(self) -> dict[str, Any]:
        return {"ok": True, "platform": self.platform.value, "note": "依赖 yt-dlp 抖音解析能力，未做登录态自检"}
