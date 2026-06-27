"""creator_loader 单元测试（纯逻辑，不依赖网络/飞书 API）。"""
from __future__ import annotations

import pytest

from hs_creator_crawler.core.models import Platform
from hs_creator_crawler.feishu.creator_loader import (
    _detect_platform,
    _extract_id,
    _flatten_fields,
    load_creators_for_platform,
)


# _flatten_fields


def test_flatten_text_field() -> None:
    fields = {"name": {"text": "李沐", "type": "text"}}
    assert _flatten_fields(fields) == {"name": "李沐"}


def test_flatten_url_field() -> None:
    fields = {"url": {"link": "https://x.com/foo", "text": "主页", "type": "url"}}
    out = _flatten_fields(fields)
    assert out["url"] == "https://x.com/foo" or out["url"] == "主页"  # 取第一个非空


def test_flatten_multi_select() -> None:
    fields = {"tags": [{"text": "AI", "id": "1"}, {"text": "技术", "id": "2"}]}
    assert _flatten_fields(fields) == {"tags": "AI, 技术"}


def test_flatten_passthrough() -> None:
    fields = {"count": 42, "name": "raw", "items": ["a", "b"]}
    out = _flatten_fields(fields)
    assert out["count"] == 42
    assert out["name"] == "raw"
    assert out["items"] == "a, b"


# _detect_platform


def test_detect_platform_by_field_value() -> None:
    record = {"fields": {"platform": {"text": "youtube"}}}
    assert _detect_platform(record) == Platform.YOUTUBE


def test_detect_platform_by_field_value_chinese() -> None:
    record = {"fields": {"平台": {"text": "B站"}}}
    assert _detect_platform(record) == Platform.BILIBILI


def test_detect_platform_by_url_bilibili() -> None:
    record = {"fields": {"主页链接": {"text": "https://space.bilibili.com/1567748478", "type": "url"}}}
    assert _detect_platform(record) == Platform.BILIBILI


def test_detect_platform_by_url_douyin() -> None:
    record = {"fields": {"url": {"text": "https://www.douyin.com/user/MS4wLjABAAAA123"}}}
    assert _detect_platform(record) == Platform.DOUYIN


def test_detect_platform_by_url_youtube_channel() -> None:
    record = {"fields": {"url": "https://www.youtube.com/channel/UCxxxx"}}
    assert _detect_platform(record) == Platform.YOUTUBE


def test_detect_platform_by_url_youtube_at() -> None:
    record = {"fields": {"url": "https://www.youtube.com/@mkbhd"}}
    assert _detect_platform(record) == Platform.YOUTUBE


def test_detect_platform_unknown() -> None:
    record = {"fields": {"name": "unknown"}}
    assert _detect_platform(record) is None


def test_detect_platform_xhs() -> None:
    record = {"fields": {"url": "https://www.xiaohongshu.com/user/profile/abc123"}}
    assert _detect_platform(record) == Platform.XIAOHONGSHU


def test_detect_platform_wechat_mp() -> None:
    record = {"fields": {"url": "https://mp.weixin.qq.com/s?__biz=MzAxxx"}}
    assert _detect_platform(record) == Platform.WECHAT_MP


# _extract_id


def test_extract_id_bilibili() -> None:
    assert _extract_id(Platform.BILIBILI, "https://space.bilibili.com/1567748478") == "1567748478"


def test_extract_id_douyin() -> None:
    assert _extract_id(Platform.DOUYIN, "https://www.douyin.com/user/MS4wLjABAAAA123") == "MS4wLjABAAAA123"


def test_extract_id_youtube_channel() -> None:
    assert _extract_id(Platform.YOUTUBE, "https://www.youtube.com/channel/UCxxxx") == "UCxxxx"


def test_extract_id_youtube_at() -> None:
    assert _extract_id(Platform.YOUTUBE, "https://www.youtube.com/@mkbhd") == "mkbhd"


def test_extract_id_unknown_returns_url() -> None:
    assert _extract_id(Platform.INSTAGRAM, "https://www.instagram.com/mkbhd/") == "https://www.instagram.com/mkbhd/"


# load_creators_for_platform with mock feishu client


class MockFeishuClient:
    """Mock 飞书客户端（用 list_records 返回预设记录）。"""

    def __init__(self, records: list[dict]) -> None:
        self._records = records
        self.calls = 0

    async def list_records(self, role: str, *, filter_expr=None):
        self.calls += 1
        return self._records


@pytest.mark.asyncio
async def test_load_creators_groups_by_platform() -> None:
    """3 个平台 4 个博主，应自动按 platform 字段分组。"""
    mock = MockFeishuClient([
        {
            "record_id": "rec_001",
            "fields": {
                "platform": {"text": "youtube"},
                "name": {"text": "MKBHD"},
                "url": {"text": "https://www.youtube.com/@mkbhd"},
                "follower_count": 19000000,
                "tags": {"text": "tech, gadgets"},
                "enabled": {"text": "true"},
            },
        },
        {
            "record_id": "rec_002",
            "fields": {
                "platform": {"text": "bilibili"},
                "name": {"text": "跟李沐学AI"},
                "url": {"text": "https://space.bilibili.com/1567748478"},
                "follower_count": 1136000,
                "enabled": {"text": "true"},
            },
        },
        {
            "record_id": "rec_003",
            "fields": {
                "platform": {"text": "douyin"},
                "name": {"text": "秋芝2046"},
                "url": {"text": "https://www.douyin.com/user/MS4wLjABAAAAwbbVuf1W2DdgRe0xCa0oxg1ZIHbzuiTzyjq3NcOVgBuu6qIidYlMYqbL3ZFY2swu"},
                "enabled": {"text": "true"},
            },
        },
        {
            "record_id": "rec_004",
            "fields": {
                "platform": {"text": "youtube"},
                "name": {"text": "Linus Tech Tips"},
                "url": {"text": "https://www.youtube.com/@LinusTechTips"},
                "enabled": {"text": "false"},  # 显式禁用
            },
        },
    ])

    grouped = await load_creators_for_platform(mock, Platform.YOUTUBE)
    assert len(grouped) == 1, "Linus Tech Tips 启用=false 应被过滤"
    assert grouped[0].name == "MKBHD"
    assert grouped[0].platform == Platform.YOUTUBE
    assert grouped[0].creator_id == "mkbhd"
    assert grouped[0].follower_count == 19000000
    assert "tech" in grouped[0].tags


@pytest.mark.asyncio
async def test_load_creators_skips_missing_fields() -> None:
    mock = MockFeishuClient([
        {
            "record_id": "rec_001",
            "fields": {
                "platform": {"text": "youtube"},
                # 缺 name / url
            },
        },
    ])
    grouped = await load_creators_for_platform(mock, Platform.YOUTUBE)
    assert len(grouped) == 0


@pytest.mark.asyncio
async def test_load_creators_url_only_infers_platform() -> None:
    """没有 platform 字段，用 URL 域名推断。"""
    mock = MockFeishuClient([
        {
            "record_id": "rec_001",
            "fields": {
                "name": {"text": "PewDiePie"},
                "url": {"text": "https://www.youtube.com/@pewdiepie"},
            },
        },
    ])
    grouped = await load_creators_for_platform(mock, Platform.YOUTUBE)
    assert len(grouped) == 1
    assert grouped[0].name == "PewDiePie"
