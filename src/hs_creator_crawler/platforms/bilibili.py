"""Bilibili 平台实现。

拉取走 bilibili-api-python（异步），下载走 yt-dlp（yt-dlp 已支持 B 站）。

后续从 dragon-hh 原仓 `download_bili_following_latest.py` 迁移更多细节（关注列表、
账号 Cookie、番剧过滤等）。当前实现保证接口完整，先跑通主路径。
"""
from __future__ import annotations

import asyncio
import importlib
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar

import yt_dlp

# 注意：bilibili_api 顶层导出了一个名为 `video` 的模块，会跟我们的方法参数名冲突。
# 这里 alias 一下，避免污染全局命名空间。
_bilibili_api_user = importlib.import_module("bilibili_api.user")
_bilibili_api_video_module = importlib.import_module("bilibili_api.video")

from ..core.exceptions import DownloadError, PlatformError
from ..core.logging import get_logger
from ..core.models import Creator, Platform, Video
from .base import PlatformCrawler, register_platform

logger = get_logger(__name__)


@register_platform
class BilibiliCrawler(PlatformCrawler):
    """Bilibili 数据采集。"""

    platform: ClassVar[Platform] = Platform.BILIBILI

    YDL_OPTS: ClassVar[dict[str, Any]] = {
        "quiet": True,
        "no_warnings": True,
        "format": "bv*[height<=1080]+ba/best",
        "merge_output_format": "mp4",
        "outtmpl": "%(id)s.%(ext)s",
        "writethumbnail": False,
    }

    async def list_videos(self, creator: Creator, limit: int) -> list[Video]:
        """通过 bilibili-api 拉取最新视频。"""
        try:
            u = _bilibili_api_user.User(uid=int(creator.creator_id))
            videos_meta = await asyncio.to_thread(u.get_videos, limit)
        except Exception as exc:
            raise PlatformError(str(exc), platform=self.platform.value) from exc

        result: list[Video] = []
        for meta in (videos_meta or {}).get("list", {}).get("vlist", [])[:limit]:
            bvid = meta.get("bvid")
            if not bvid:
                continue
            pubdate = datetime.fromtimestamp(meta.get("created", 0))
            result.append(
                Video(
                    platform=self.platform,
                    video_id=bvid,
                    creator_id=creator.creator_id,
                    title=meta.get("title", ""),
                    description=meta.get("description", ""),
                    url=f"https://www.bilibili.com/video/{bvid}",
                    published_at=pubdate,
                    duration_seconds=int(meta.get("length", 0)),
                    view_count=int(meta.get("play", 0)),
                    like_count=int(meta.get("like", 0)),
                    comment_count=int(meta.get("comment", 0)),
                )
            )
        self.log.info("list_videos_done", creator=creator.name, count=len(result))
        return result

    async def download_video(self, video: Video, output_dir: Path) -> Path:
        """下载视频到 output_dir，文件名 `{bvid}.mp4`。"""
        output_dir.mkdir(parents=True, exist_ok=True)

        def _do_download() -> Path:
            opts = {**self.YDL_OPTS, "outtmpl": str(output_dir / "%(id)s.%(ext)s")}
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    ydl.download([str(video.url)])
            except Exception as exc:
                raise DownloadError(str(exc), platform=self.platform.value, video_id=video.video_id) from exc
            candidates = sorted(output_dir.glob(f"{video.video_id}.*"))
            for ext in (".mp4", ".mkv", ".flv"):
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

    async def fetch_subtitle(self, video: Video) -> str | None:
        """尝试拿官方 CC 字幕（中文优先）。失败时返回 None，调用方应回退到 ASR。"""
        try:
            v = _bilibili_api_video_module.Video(bvid=video.video_id)
            info = await asyncio.to_thread(v.get_info)
            subtitles = info.get("data", {}).get("subtitle", {}).get("subtitles", [])
            for sub in subtitles:
                if sub.get("lan") in ("zh-CN", "zh-Hans", "ai-zh", "zh"):
                    sub_url = sub.get("subtitle_url")
                    if sub_url:
                        import httpx

                        async with httpx.AsyncClient() as client:
                            r = await client.get(sub_url, timeout=10)
                            r.raise_for_status()
                            data = r.json()
                            return "\n".join(item.get("content", "") for item in data.get("body", []))
        except Exception as exc:
            self.log.debug("fetch_subtitle_failed", video_id=video.video_id, error=str(exc))
        return None

    async def health_check(self) -> dict[str, Any]:
        try:
            u = _bilibili_api_user.User(uid=2)  # 哔哩哔哩官方号，永远存在
            await asyncio.to_thread(u.get_user_info)
            return {"ok": True, "platform": self.platform.value}
        except Exception as exc:
            return {"ok": False, "platform": self.platform.value, "error": str(exc)}
