"""核心数据模型（Pydantic v2）。

横跨所有平台/所有 pipeline 阶段，确保 manifest 序列化与跨进程通信的稳定性。
"""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class Platform(StrEnum):
    BILIBILI = "bilibili"
    DOUYIN = "douyin"
    YOUTUBE = "youtube"
    XIAOHONGSHU = "xiaohongshu"
    WECHAT_MP = "wechat_mp"
    WECHAT_SPH = "wechat_sph"  # 视频号
    X_TWITTER = "x_twitter"
    THREADS = "threads"
    INSTAGRAM = "instagram"
    TIKTOK_INTL = "tiktok_intl"


class ASRBackend(StrEnum):
    """ASR 后端。Apple Silicon M4 走 mlx-whisper / mlx-audio，云端走 OpenAI / DashScope。"""

    MLX_WHISPER = "mlx_whisper"  # macOS 本地（MPS 后端）
    MLX_AUDIO = "mlx_audio"  # macOS 本地（Qwen3-ASR via mlx-audio）
    OPENAI_WHISPER = "openai_whisper"  # 云端 OpenAI
    DASHSCOPE_QWEN = "dashscope_qwen"  # 阿里百炼 Qwen-ASR
    NONE = "none"  # 跳过转录


class Creator(BaseModel):
    """被追踪的博主。"""

    model_config = ConfigDict(frozen=True)

    platform: Platform
    creator_id: str  # 平台内唯一 ID（B 站 UID / 抖音 sec_uid / YouTube channel_id）
    name: str
    url: HttpUrl
    avatar_url: HttpUrl | None = None
    follower_count: int | None = None
    tags: list[str] = Field(default_factory=list)
    enabled: bool = True
    last_polled_at: datetime | None = None
    notes: str = ""


class Video(BaseModel):
    """博主发布的单个视频。"""

    model_config = ConfigDict(frozen=False)

    platform: Platform
    video_id: str
    creator_id: str
    title: str
    description: str = ""
    url: HttpUrl
    published_at: datetime
    duration_seconds: int = 0
    view_count: int = 0
    like_count: int = 0
    comment_count: int = 0
    share_count: int = 0
    cover_url: HttpUrl | None = None
    local_video_path: str | None = None  # 本地下载路径（绝对路径）
    local_audio_path: str | None = None  # 抽出的音频
    transcript_path: str | None = None  # 转录结果（.txt / .srt）
    transcript_text: str | None = None  # 内存里的纯文本
    transcript_segments: list[dict[str, Any]] = Field(default_factory=list)
    asr_backend: ASRBackend = ASRBackend.NONE
    feishu_record_id: str | None = None  # 同步到飞书后的 record_id
    feishu_doc_url: HttpUrl | None = None  # 飞书 Docs URL
    last_synced_at: datetime | None = None


class VideoComment(BaseModel):
    """视频评论（按平台差异化字段在子类里补）。"""

    model_config = ConfigDict(frozen=True)

    platform: Platform
    video_id: str
    comment_id: str
    author: str
    text: str
    like_count: int = 0
    published_at: datetime | None = None


class StepResult(BaseModel):
    """单步执行结果（manifest 序列化友好）。"""

    model_config = ConfigDict(frozen=False)

    name: str
    started_at: datetime
    ended_at: datetime
    returncode: int
    elapsed_seconds: float
    summary: dict[str, Any] = Field(default_factory=dict)
    error_message: str | None = None
    manifest_paths: list[str] = Field(default_factory=list)


class PipelineResult(BaseModel):
    """整个 pipeline 的最终结果。"""

    model_config = ConfigDict(frozen=False)

    started_at: datetime
    ended_at: datetime | None = None
    platform: Platform
    dry_run: bool
    steps: list[StepResult] = Field(default_factory=list)
    failures: list[dict[str, Any]] = Field(default_factory=list)
    manifest_paths: dict[str, str] = Field(default_factory=dict)
    summaries: dict[str, Any] = Field(default_factory=dict)
    videos_downloaded: int = 0
    videos_transcribed: int = 0
    videos_synced_to_feishu: int = 0
    comments_synced_to_feishu: int = 0
    docs_published_to_feishu: int = 0

    @property
    def success(self) -> bool:
        return len(self.failures) == 0

    def to_manifest_summary(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "platform": self.platform.value,
            "dry_run": self.dry_run,
            "success": self.success,
            "videos_downloaded": self.videos_downloaded,
            "videos_transcribed": self.videos_transcribed,
            "videos_synced_to_feishu": self.videos_synced_to_feishu,
            "comments_synced_to_feishu": self.comments_synced_to_feishu,
            "docs_published_to_feishu": self.docs_published_to_feishu,
            "failed_steps": [f["step"] for f in self.failures],
        }
