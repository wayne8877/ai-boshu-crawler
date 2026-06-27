"""统一日志（structlog 优先，stdlib fallback）。

structlog 装了就用（JSON / 结构化输出），没装时降级到 stdlib logging（同样 JSON 可读）。
所有 module 通过 `get_logger(__name__)` 拿 logger。
"""
from __future__ import annotations

import logging
import sys
from typing import Any

try:
    import structlog

    _HAS_STRUCTLOG = True
except ImportError:  # pragma: no cover - 依赖未装场景
    _HAS_STRUCTLOG = False


def configure_logging(level: str = "INFO", *, json_only: bool = False) -> None:
    """配置全局 logging。

    Args:
        level: 日志级别（DEBUG / INFO / WARNING / ERROR）。
        json_only: True 时只输出 JSON（cron / 容器场景）。
    """
    log_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        level=log_level,
        stream=sys.stdout,
        force=True,
    )

    if not _HAS_STRUCTLOG:
        return

    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=False)
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        timestamper,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    if json_only:
        processors = shared_processors + [structlog.processors.JSONRenderer()]
    else:
        processors = shared_processors + [structlog.dev.ConsoleRenderer(colors=True)]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> Any:
    """拿到 logger。优先 structlog，降级到 stdlib。"""
    if _HAS_STRUCTLOG:
        return structlog.get_logger(name)
    return logging.getLogger(name)
