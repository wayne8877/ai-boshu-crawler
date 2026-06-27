"""YouTube 平台实现。

下载走 yt-dlp，转录走 ASR 层（macOS M4 优先 mlx-whisper）。

频道来源优先级：
1. 飞书 Base `creators` 表（platform=youtube）
2. 本地配置文件（fallback）
3. 代码内常量（开发兜底）
"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar

import yt_dlp

from ..core.exceptions import DownloadError, PlatformAuthError, PlatformError, PlatformRateLimitError
from ..core.logging import get_logger
from ..core.models import Creator, Platform, Video
from .base import PlatformCrawler, register_platform

logger = get_logger(__name__)


def parse_youtube_duration(iso8601: str | None) -> int:
    """yt-dlp 返回的 duration 字段（ISO 8601 / 秒数）解析为秒数。"""
    if iso8601 is None:
        return 0
    if isinstance(iso8601, (int, float)):
        return int(iso8601)
    match = re.match(r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$", iso8601)
    if match:
        h, m, s = match.groups()
        return int(h or 0) * 3600 + int(m or 0) * 60 + int(s or 0)
    return 0


def _classify_yt_dlp_error(exc: Exception) -> type:
    """根据 yt-dlp 异常信息分类（避免依赖 yt_dlp.utils 的非公开子模块）。"""
    msg = str(exc).lower()
    if "429" in msg or "rate limit" in msg:
        return PlatformRateLimitError
    if "sign in" in msg or "bot" in msg or "captcha" in msg:
        return PlatformAuthError
    return PlatformError


@register_platform
class YouTubeCrawler(PlatformCrawler):
    """YouTube 数据采集。"""

    platform: ClassVar[Platform] = Platform.YOUTUBE

    YDL_LIST_OPTS: ClassVar[dict[str, Any]] = {
        "quiet": True,
        "skip_download": True,
        "extract_flat": "in_playlist",
        "ignoreerrors": True,
    }

    YDL_DOWNLOAD_OPTS: ClassVar[dict[str, Any]] = {
        "quiet": True,
        "no_warnings": True,
        "format": "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080][ext=mp4]",
        "merge_output_format": "mp4",
        "outtmpl": "%(id)s.%(ext)s",
        "writethumbnail": False,
        "writesubtitles": False,
        "writeautomaticsub": False,
    }

    def _extract(self, url: str, opts: dict[str, Any]) -> dict[str, Any]:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return dict(info) if info else {}

    async def list_videos(self, creator: Creator, limit: int) -> list[Video]:
        """拉取频道最新视频元数据。"""
        url_str = str(creator.url)
        url = url_str if url_str.startswith("http") else f"https://www.youtube.com/channel/{creator.creator_id}"
        opts = {**self.YDL_LIST_OPTS, "playlistend": limit}
        try:
            info = await asyncio.to_thread(self._extract, url, opts)
        except yt_dlp.utils.DownloadError as exc:
            err_cls = _classify_yt_dlp_error(exc)
            raise err_cls(str(exc), platform=self.platform.value) from exc

        entries = info.get("entries") or []
        videos: list[Video] = []
        for entry in entries[:limit]:
            if not entry:
                continue
            video_id = entry.get("id")
            if not video_id:
                continue
            videos.append(
                Video(
                    platform=self.platform,
                    video_id=video_id,
                    creator_id=creator.creator_id,
                    title=entry.get("title") or "",
                    description=entry.get("description") or "",
                    url=f"https://www.youtube.com/watch?v={video_id}",
                    published_at=datetime.fromtimestamp(entry.get("timestamp") or 0, tz=timezone.utc),
                    duration_seconds=int(entry.get("duration") or 0) or parse_youtube_duration(entry.get("duration_string")),
                    view_count=int(entry.get("view_count") or 0),
                )
            )
        self.log.info("list_videos_done", creator=creator.name, count=len(videos))
        return videos

    async def download_video(self, video: Video, output_dir: Path) -> Path:
        """下载视频到 output_dir。文件名 `{video_id}.mp4`。"""
        output_dir.mkdir(parents=True, exist_ok=True)
        opts: dict[str, Any] = {
            **self.YDL_DOWNLOAD_OPTS,
            "outtmpl": str(output_dir / "%(id)s.%(ext)s"),
            "paths": {"home": str(output_dir)},
        }

        def _do_download() -> Path:
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(str(video.url), download=True)
                    if not info:
                        raise DownloadError("yt-dlp 返回空 info", platform=self.platform.value, video_id=video.video_id)
                    video_id = info.get("id") or video.video_id
                    request_filename = info.get("requested_downloads", [{}])[0].get("filepath")
                    if request_filename and Path(request_filename).exists():
                        return Path(request_filename)
                    candidates = sorted(output_dir.glob(f"{video_id}.*"))
                    media = [c for c in candidates if c.suffix in {".mp4", ".mkv", ".webm"}]
                    if media:
                        return media[0]
                    raise DownloadError(
                        f"找不到下载产物 in {output_dir}", platform=self.platform.value, video_id=video.video_id
                    )
            except yt_dlp.utils.DownloadError as exc:
                err_cls = _classify_yt_dlp_error(exc)
                raise err_cls(str(exc), platform=self.platform.value, video_id=video.video_id) from exc

        path = await asyncio.to_thread(_do_download)
        self.log.info("download_done", video_id=video.video_id, path=str(path), size_mb=path.stat().st_size / 1024 / 1024)
        return path

    async def health_check(self) -> dict[str, Any]:
        try:
            info = await asyncio.to_thread(
                self._extract,
                "https://www.youtube.com/feed/trending",
                {**self.YDL_LIST_OPTS, "playlistend": 1},
            )
            ok = bool(info.get("entries"))
            return {"ok": ok, "platform": self.platform.value, "trending_count": len(info.get("entries") or [])}
        except Exception as exc:
            return {"ok": False, "platform": self.platform.value, "error": str(exc)}


def load_youtube_creators_from_json(path: Path | str) -> list[Creator]:
    """从 JSON 文件加载 YouTube 博主列表（开发模式 fallback）。

    JSON 格式：[{"creator_id": "UC...", "name": "...", "url": "...", "tags": [...]}]
    """
    path = Path(path)
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [
        Creator(
            platform=Platform.YOUTUBE,
            creator_id=item["creator_id"],
            name=item["name"],
            url=item["url"],
            tags=item.get("tags", []),
            follower_count=item.get("follower_count"),
        )
        for item in data
    ]
