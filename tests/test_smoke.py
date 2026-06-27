"""Smoke 测试：模块 import + 配置加载 + 平台注册表自检。

不依赖外部 API，仅验证代码骨架能跑。
"""
from __future__ import annotations

from pathlib import Path

import pytest


def test_core_imports() -> None:
    from hs_creator_crawler.core import (
        ASRConfig,
        ASRBackend,
        Creator,
        CreatorTrackerConfig,
        FeishuConfig,
        PipelineResult,
        Platform,
        Video,
        configure_logging,
        get_logger,
    )

    assert Platform.BILIBILI == "bilibili"
    assert Platform.YOUTUBE == "youtube"
    assert ASRBackend.MLX_WHISPER == "mlx_whisper"


def test_config_load_default() -> None:
    """无配置文件时走默认值加载。"""
    from hs_creator_crawler.core import CreatorTrackerConfig, DEFAULT_CONFIG_PATH

    # 仅在 ~/.config/hs-creator-crawler/config.json 存在时验证文件加载；否则验证默认值
    if DEFAULT_CONFIG_PATH.exists():
        cfg = CreatorTrackerConfig.load()
        assert cfg is not None
    else:
        # 直接构造验证 Pydantic schema
        cfg = CreatorTrackerConfig()
        assert cfg.log_level in {"DEBUG", "INFO", "WARNING", "ERROR"}
        assert cfg.asr.fallback is not None


def test_platforms_registered() -> None:
    """3 个平台必须已注册。"""
    from hs_creator_crawler.platforms import PLATFORM_REGISTRY
    from hs_creator_crawler.core.models import Platform

    assert Platform.BILIBILI in PLATFORM_REGISTRY
    assert Platform.DOUYIN in PLATFORM_REGISTRY
    assert Platform.YOUTUBE in PLATFORM_REGISTRY


def test_cli_help() -> None:
    """CLI help 不崩。"""
    from hs_creator_crawler.bin.hs_crawler import parse_args

    # argparse 应当能解析 --help 之外的最小命令
    args = parse_args(["list-platforms"])
    assert args.command == "list-platforms"


def test_asr_backend_registry() -> None:
    from hs_creator_crawler.asr.backends import _BACKENDS, get_asr_backend
    from hs_creator_crawler.core import ASRBackend, ASRConfig

    assert ASRBackend.MLX_WHISPER in _BACKENDS
    assert ASRBackend.OPENAI_WHISPER in _BACKENDS
    assert ASRBackend.NONE in _BACKENDS

    # NoOp 后端必须可用（无外部依赖）
    backend = get_asr_backend(ASRBackend.NONE, ASRConfig())
    ok, _ = backend.is_available()
    assert ok is True


def test_example_config_writable(tmp_path: Path) -> None:
    """example_config() 必须能写出可解析的 JSON。"""
    import json

    from hs_creator_crawler.core import example_config

    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(example_config(), ensure_ascii=False, indent=2), encoding="utf-8")
    data = json.loads(config_path.read_text(encoding="utf-8"))
    assert "feishu" in data
    assert "asr" in data
    assert data["asr"]["primary"] in {"mlx_whisper", "mlx_audio", "openai_whisper", "dashscope_qwen", "none"}


def test_video_model_validation() -> None:
    """Video 模型的 Pydantic 校验。"""
    from datetime import datetime

    from hs_creator_crawler.core import Platform, Video

    v = Video(
        platform=Platform.YOUTUBE,
        video_id="dQw4w9WgXcQ",
        creator_id="UC_x5XG1OV2P6uZZ5FSM9Ttw",
        title="Never Gonna Give You Up",
        url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        published_at=datetime(2009, 10, 25),
    )
    assert v.platform == Platform.YOUTUBE
    assert v.asr_backend.value == "none"


@pytest.mark.asyncio
async def test_feishu_client_lazy() -> None:
    """飞书客户端无凭证时不应崩溃。"""
    from hs_creator_crawler.core import CreatorTrackerConfig
    from hs_creator_crawler.feishu import FeishuBaseClient

    cfg = CreatorTrackerConfig()  # 无 app_id/secret
    client = FeishuBaseClient(cfg)
    assert client is not None
    # upsert_video 在表未配置时应 graceful return None
    from hs_creator_crawler.core import Platform, Video
    from datetime import datetime

    v = Video(
        platform=Platform.BILIBILI,
        video_id="BV1xx",
        creator_id="123",
        title="t",
        url="https://www.bilibili.com/video/BV1xx",
        published_at=datetime.now(),
    )
    record_id = await client.upsert_video(v)
    assert record_id is None  # 表未配置
