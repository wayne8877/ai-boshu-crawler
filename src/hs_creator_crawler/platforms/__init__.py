"""平台实现自动注册。

import 这个模块会触发 @register_platform 装饰器执行，把所有平台注册到 PLATFORM_REGISTRY。

平台子模块在 import 时若缺第三方依赖（如 yt_dlp / bilibili_api / lark-oapi），会**降级**
记录 warning 而不是抛出，让核心层（core/ + CLI）在依赖未装时仍可启动。
"""

from __future__ import annotations

import importlib
import sys

from ..core.logging import get_logger

log = get_logger(__name__)

# 平台实现按"安全导入"方式加载（缺第三方依赖时降级 warning）
_AVAILABLE_PLATFORMS: list[str] = []
_FAILED_PLATFORMS: dict[str, str] = {}

for _platform_name, _module_name in [
    ("bilibili", "hs_creator_crawler.platforms.bilibili"),
    ("douyin", "hs_creator_crawler.platforms.douyin"),
    ("youtube", "hs_creator_crawler.platforms.youtube"),
]:
    try:
        importlib.import_module(_module_name)
        _AVAILABLE_PLATFORMS.append(_platform_name)
    except (ImportError, ModuleNotFoundError) as exc:
        _FAILED_PLATFORMS[_platform_name] = str(exc)
        # 用 print 而非 logger.warning（兼容 stdlib logging 和 structlog 两种风格）
        print(
            f"[hs_creator_crawler] WARNING: platform '{_platform_name}' disabled: {exc}. "
            f"pip install 相关依赖即可启用。",
            file=sys.stderr,
        )

from .base import PLATFORM_REGISTRY, PlatformCrawler, get_crawler, register_platform  # noqa: E402

__all__ = [
    "PLATFORM_REGISTRY",
    "PlatformCrawler",
    "_AVAILABLE_PLATFORMS",
    "_FAILED_PLATFORMS",
    "get_crawler",
    "register_platform",
]  # BilibiliCrawler / DouyinCrawler / YouTubeCrawler 视 import 情况
