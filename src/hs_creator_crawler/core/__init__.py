"""hs_creator_crawler 核心层。

横跨所有子系统的共享基础设施：配置 / 模型 / 日志 / 异常。
"""

from .config import (
    ASRConfig,
    CreatorTrackerConfig,
    DEFAULT_CONFIG_PATH,
    DEFAULT_DOWNLOADS_ROOT,
    FeishuBaseTableConfig,
    FeishuConfig,
    PipelineDefaults,
    StorageConfig,
    example_config,
    write_example_config,
)
from .exceptions import (
    ASRBackendUnavailableError,
    ConfigError,
    DownloadError,
    FeishuAuthError,
    FeishuBaseSchemaError,
    FeishuError,
    FeishuRateLimitError,
    HSCrawlerError,
    PlatformAuthError,
    PlatformError,
    PlatformNotSupportedError,
    PlatformRateLimitError,
    TranscriptionError,
)
from .logging import configure_logging, get_logger
from .models import (
    ASRBackend,
    Creator,
    PipelineResult,
    Platform,
    StepResult,
    Video,
    VideoComment,
)

__all__ = [
    # config
    "ASRConfig",
    "CreatorTrackerConfig",
    "DEFAULT_CONFIG_PATH",
    "DEFAULT_DOWNLOADS_ROOT",
    "FeishuBaseTableConfig",
    "FeishuConfig",
    "PipelineDefaults",
    "StorageConfig",
    "example_config",
    "write_example_config",
    # exceptions
    "ASRBackendUnavailableError",
    "ConfigError",
    "DownloadError",
    "FeishuAuthError",
    "FeishuBaseSchemaError",
    "FeishuError",
    "FeishuRateLimitError",
    "HSCrawlerError",
    "PlatformAuthError",
    "PlatformError",
    "PlatformNotSupportedError",
    "PlatformRateLimitError",
    "TranscriptionError",
    # logging
    "configure_logging",
    "get_logger",
    # models
    "ASRBackend",
    "Creator",
    "PipelineResult",
    "Platform",
    "StepResult",
    "Video",
    "VideoComment",
]
