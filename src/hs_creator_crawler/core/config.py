"""统一配置（pydantic-settings）。

按优先级：CLI 参数 > 环境变量 > 配置文件 > 默认值。

配置加载顺序：
1. CLI --config <path> 指定文件
2. 环境变量 HS_CC_* 覆盖
3. 配置文件（YAML 或 JSON）
4. 内置默认

向后兼容 dragon-hh 原仓 `feishu-base-config.json` 格式 —— 自动迁移到新 schema。
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .exceptions import ConfigError
from .models import ASRBackend, Platform

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "hs-creator-crawler" / "config.json"
DEFAULT_DOWNLOADS_ROOT = Path.home() / "Downloads" / "hs-creator-crawler"


class FeishuBaseTableConfig(BaseModel):
    """飞书 Base 单张表的 schema 映射。"""

    model_config = ConfigDict(extra="ignore")

    app_token: str
    table_id: str
    name: str | None = None  # 人类可读的名字（仅日志用）

    # 字段名映射：本系统字段 → 飞书字段
    # 缺字段时整个 pipeline 优雅降级（log warning 跳过该字段，不阻塞）
    field_mapping: dict[str, str] = Field(default_factory=dict)


class FeishuConfig(BaseModel):
    """飞书接入配置。

    接入合盛现有体系（hesheng-feishu-bitables）：app_id/secret 在外部环境变量。
    """

    model_config = ConfigDict(extra="ignore")

    app_id: str | None = None  # 留 None 时从 env HS_CC_FEISHU_APP_ID 读
    app_secret: str | None = None  # 同上
    base_url: str = "https://open.feishu.cn/open-apis"

    # 合盛 Base 表（app_token + table_id）
    tables: dict[str, FeishuBaseTableConfig] = Field(default_factory=dict)

    @field_validator("tables")
    @classmethod
    def _validate_table_keys(cls, v: dict[str, FeishuBaseTableConfig]) -> dict[str, FeishuBaseTableConfig]:
        valid = {"creators", "videos", "comments", "metric_snapshots", "crawl_task_logs", "transcript_docs"}
        bad = set(v) - valid
        if bad:
            raise ConfigError(f"飞书表 key 非法: {bad}. 合法值: {sorted(valid)}")
        return v


class ASRConfig(BaseModel):
    """ASR 后端配置。"""

    model_config = ConfigDict(extra="ignore")

    # 主后端（M4 优先 MLX_WHISPER / MLX_AUDIO，云端优先 DASHSCOPE_QWEN）
    primary: ASRBackend = ASRBackend.MLX_WHISPER
    # 兜底后端（主后端失败时自动切换）
    fallback: ASRBackend | None = ASRBackend.OPENAI_WHISPER
    # 模型（按后端不同而不同）
    primary_model: str = "mlx-community/whisper-large-v3-turbo"
    fallback_model: str = "whisper-1"
    # 单个视频最长转录时长（秒），超过则跳过并警告
    max_duration_seconds: int = 3600
    # 云端 API key（OpenAI / DashScope）
    openai_api_key: str | None = None
    dashscope_api_key: str | None = None


class StorageConfig(BaseModel):
    """本地存储路径。"""

    model_config = ConfigDict(extra="ignore")

    downloads_root: Path = DEFAULT_DOWNLOADS_ROOT
    manifests_subdir: str = "manifests"
    transcripts_subdir: str = "transcripts"
    videos_subdir: str = "videos"
    audio_subdir: str = "audio"
    logs_subdir: str = "logs"


class PipelineDefaults(BaseModel):
    """pipeline 默认参数（CLI flag 可覆盖）。"""

    model_config = ConfigDict(extra="ignore")

    videos_per_creator: int = 3
    max_creators: int | None = None
    max_total_videos: int | None = None
    retries: int = 2
    only_today: bool = False
    transcribe: bool = True
    sync_to_feishu: bool = True
    enrich: bool = True
    publish_docs: bool = False
    skip_existing: bool = True
    skip_existing_in_feishu: bool = True
    stop_on_error: bool = False
    dry_run: bool = False


class CreatorTrackerConfig(BaseSettings):
    """全局配置根。"""

    model_config = SettingsConfigDict(
        env_prefix="HS_CC_",
        env_nested_delimiter="_",
        case_sensitive=False,
        extra="ignore",
    )

    # 平台启用开关
    enabled_platforms: list[Platform] = Field(
        default_factory=lambda: [Platform.BILIBILI, Platform.DOUYIN, Platform.YOUTUBE]
    )

    # 子模块配置
    feishu: FeishuConfig = Field(default_factory=FeishuConfig)
    asr: ASRConfig = Field(default_factory=ASRConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    pipeline_defaults: PipelineDefaults = Field(default_factory=PipelineDefaults)

    # 通用
    log_level: str = "INFO"
    log_json_only: bool = False
    request_timeout_seconds: float = 30.0
    request_max_concurrent: int = 4

    @classmethod
    def load(cls, config_path: Path | str | None = None) -> "CreatorTrackerConfig":
        """从 YAML/JSON 文件 + 环境变量加载。

        优先级：CLI 参数 > 环境变量 HS_CC_* > 配置文件 > 默认值。
        """
        path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
        data: dict[str, Any] = {}

        if path.exists():
            try:
                text = path.read_text(encoding="utf-8")
                if path.suffix in {".yaml", ".yml"}:
                    import yaml  # type: ignore[import-untyped]

                    data = yaml.safe_load(text) or {}
                else:
                    data = json.loads(text)
            except Exception as exc:
                raise ConfigError(f"配置加载失败 {path}: {exc}") from exc

        # 兼容 dragon-hh 原 feishu-base-config.json 格式
        if "tables" in data and "feishu" not in data:
            data["feishu"] = {
                "app_id": data.get("app_id"),
                "app_secret": data.get("app_secret"),
                "base_url": data.get("base_url", "https://open.feishu.cn/open-apis"),
                "tables": data["tables"],
            }

        return cls(**data)

    def ensure_storage_dirs(self) -> None:
        """确保所有 storage 子目录存在。"""
        self.storage.downloads_root.mkdir(parents=True, exist_ok=True)
        (self.storage.downloads_root / self.storage.manifests_subdir).mkdir(parents=True, exist_ok=True)
        (self.storage.downloads_root / self.storage.transcripts_subdir).mkdir(parents=True, exist_ok=True)
        (self.storage.downloads_root / self.storage.videos_subdir).mkdir(parents=True, exist_ok=True)
        (self.storage.downloads_root / self.storage.audio_subdir).mkdir(parents=True, exist_ok=True)
        (self.storage.downloads_root / self.storage.logs_subdir).mkdir(parents=True, exist_ok=True)


def example_config() -> dict[str, Any]:
    """生成示例配置（首次安装时给用户参考）。"""
    return {
        "enabled_platforms": ["bilibili", "douyin", "youtube"],
        "feishu": {
            "app_id": "cli_xxxx",
            "app_secret": "xxxx",
            "tables": {
                "creators": {
                    "app_token": "bascnXXXX",
                    "table_id": "tblXXXX",
                    "name": "博主主表",
                    "field_mapping": {
                        "platform": "平台",
                        "creator_id": "博主 ID",
                        "name": "博主名",
                        "url": "主页链接",
                        "follower_count": "粉丝数",
                        "tags": "标签",
                        "last_polled_at": "最近抓取",
                    },
                },
                "videos": {
                    "app_token": "bascnXXXX",
                    "table_id": "tblYYYY",
                    "name": "视频表",
                    "field_mapping": {
                        "platform": "平台",
                        "video_id": "视频 ID",
                        "creator_id": "博主 ID",
                        "title": "标题",
                        "url": "视频链接",
                        "published_at": "发布时间",
                        "duration_seconds": "时长(秒)",
                        "view_count": "播放量",
                        "like_count": "点赞数",
                        "comment_count": "评论数",
                        "local_video_path": "本地路径",
                        "transcript_text": "转录全文",
                        "asr_backend": "转录引擎",
                    },
                },
                "comments": {
                    "app_token": "bascnXXXX",
                    "table_id": "tblZZZZ",
                    "name": "评论表",
                },
                "crawl_task_logs": {
                    "app_token": "bascnXXXX",
                    "table_id": "tblWWWW",
                    "name": "任务日志表",
                },
            },
        },
        "asr": {
            "primary": "mlx_whisper",
            "fallback": "openai_whisper",
            "primary_model": "mlx-community/whisper-large-v3-turbo",
            "fallback_model": "whisper-1",
            "max_duration_seconds": 3600,
        },
        "storage": {
            "downloads_root": str(DEFAULT_DOWNLOADS_ROOT),
        },
        "pipeline_defaults": {
            "videos_per_creator": 3,
            "transcribe": True,
            "sync_to_feishu": True,
        },
    }


def write_example_config(path: Path | str) -> None:
    """写出示例配置文件。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(example_config(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# 模块初始化时把 ~/.config/hs-creator-crawler 加到 PATH 候选
os.environ.setdefault("XDG_CONFIG_HOME", str(Path.home() / ".config"))
