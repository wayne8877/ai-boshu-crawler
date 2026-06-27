"""自定义异常体系。

所有平台/ASR/飞书错误都从 HSCrawlerError 派生，按层级 catch。
"""
from __future__ import annotations


class HSCrawlerError(Exception):
    """根异常。所有 hs-creator-crawler 异常继承此类。"""


class ConfigError(HSCrawlerError):
    """配置加载/校验失败（飞书凭证缺失、博主列表为空、ASR 模型未指定等）。"""


class PlatformError(HSCrawlerError):
    """平台层错误基类。"""

    def __init__(self, message: str, *, platform: str, video_id: str | None = None) -> None:
        super().__init__(message)
        self.platform = platform
        self.video_id = video_id


class PlatformNotSupportedError(PlatformError):
    """请求的平台未实现或未在 platforms/__init__.py 注册。"""


class PlatformAuthError(PlatformError):
    """平台登录态失效 / Cookie 过期 / 触发验证码。"""


class PlatformRateLimitError(PlatformError):
    """平台限流（HTTP 429 / 风控）。"""

    def __init__(self, message: str, *, platform: str, retry_after: float = 0, video_id: str | None = None) -> None:
        super().__init__(message, platform=platform, video_id=video_id)
        self.retry_after = retry_after


class DownloadError(PlatformError):
    """视频下载失败（yt-dlp / ffmpeg / 网络错误）。"""


class TranscriptionError(HSCrawlerError):
    """ASR 转录失败。"""

    def __init__(self, message: str, *, backend: str, audio_path: str | None = None) -> None:
        super().__init__(message)
        self.backend = backend
        self.audio_path = audio_path


class ASRBackendUnavailableError(TranscriptionError):
    """ASR 后端不可用（MPS 启动失败 / 云端 API Key 缺失）。"""


class FeishuError(HSCrawlerError):
    """飞书层错误基类。"""


class FeishuAuthError(FeishuError):
    """飞书 access_token 过期 / tenant_access_token 失败。"""


class FeishuBaseSchemaError(FeishuError):
    """飞书 Base 表 schema 不匹配（缺字段 / 字段类型错）。"""


class FeishuRateLimitError(FeishuError):
    """飞书 API 限流（QPS 超限）。"""

    def __init__(self, message: str, *, retry_after: float = 0) -> None:
        super().__init__(message)
        self.retry_after = retry_after
