"""从飞书 Base `creators` 表拉博主列表（合盛体系接入关键能力）。

按 platform 字段自动分组，把飞书记录翻译成 Creator 模型。

优先级：飞书 Base > 本地 JSON 兜底 > 空（pipeline skip 该平台）。
"""
from __future__ import annotations

from typing import Iterable

from ..core.exceptions import FeishuBaseSchemaError
from ..core.logging import get_logger
from ..core.models import Creator, Platform

logger = get_logger(__name__)


# 飞书 record.fields 嵌套 key 处理
def _flatten_fields(fields: dict) -> dict:
    """飞书返回的字段值可能是 {'text': '...', 'type': 'text'} 嵌套，扁平化。"""
    out = {}
    for k, v in fields.items():
        if isinstance(v, dict):
            # 飞书的 text/url/select 字段都有这种结构
            out[k] = v.get("text") or v.get("url") or v.get("name") or v.get("value") or ""
        elif isinstance(v, list):
            # multi_select / array 字段
            if v and isinstance(v[0], dict):
                out[k] = ", ".join(
                    item.get("text") or item.get("name") or item.get("value") or ""
                    for item in v
                )
            else:
                out[k] = ", ".join(str(item) for item in v)
        else:
            out[k] = v
    return out


# 平台识别
PLATFORM_KEYWORDS = {
    Platform.BILIBILI: ["bili", "bilibili", "b站", "哔哩"],
    Platform.DOUYIN: ["douyin", "抖音"],
    Platform.YOUTUBE: ["youtube", "yt", "油管"],
    Platform.XIAOHONGSHU: ["xhs", "xiaohongshu", "小红书"],
    Platform.WECHAT_MP: ["wechat_mp", "公众号", "wechat"],
    Platform.WECHAT_SPH: ["wechat_sph", "视频号", "sph"],
    Platform.X_TWITTER: ["x", "twitter", "推特"],
    Platform.THREADS: ["threads"],
    Platform.INSTAGRAM: ["instagram", "ig", "ins"],
    Platform.TIKTOK_INTL: ["tiktok", "tiktok国际"],
}


def _detect_platform(record: dict, fallback: Platform | None = None) -> Platform | None:
    """从飞书 record 里识别平台（多种兼容字段：platform / 平台 / source 等）。"""
    fields = _flatten_fields(record.get("fields", {}))

    # 1. 直接匹配
    raw = (fields.get("platform") or fields.get("平台") or fields.get("source") or "").strip().lower()
    if raw:
        for plat, keywords in PLATFORM_KEYWORDS.items():
            if raw in [k.lower() for k in keywords]:
                return plat
        # 直接 enum 匹配
        for plat in Platform:
            if raw == plat.value:
                return plat

    # 2. fallback: 用 URL 域名识别
    url = (fields.get("url") or fields.get("主页链接") or fields.get("主页") or "").lower()
    if "bilibili.com" in url:
        return Platform.BILIBILI
    if "douyin.com" in url or "iesdouyin.com" in url:
        return Platform.DOUYIN
    if "youtube.com" in url or "youtu.be" in url:
        return Platform.YOUTUBE
    if "xiaohongshu.com" in url or "xhslink.com" in url:
        return Platform.XIAOHONGSHU
    if "mp.weixin.qq.com" in url:
        return Platform.WECHAT_MP
    if "channels.weixin.qq.com" in url:
        return Platform.WECHAT_SPH
    if "x.com" in url or "twitter.com" in url:
        return Platform.X_TWITTER
    if "threads.net" in url:
        return Platform.THREADS
    if "instagram.com" in url:
        return Platform.INSTAGRAM
    if "tiktok.com" in url:
        return Platform.TIKTOK_INTL

    return fallback


def _extract_id(platform: Platform, url: str) -> str:
    """从 URL 提取平台内 creator_id（URL 是最稳的 ID 来源）。"""
    import re

    url = url.strip()
    if platform == Platform.BILIBILI:
        m = re.search(r"space\.bilibili\.com/(\d+)", url)
        if m:
            return m.group(1)
    if platform == Platform.DOUYIN:
        m = re.search(r"/user/([^/?]+)", url)
        if m:
            return m.group(1)
    if platform == Platform.YOUTUBE:
        m = re.search(r"youtube\.com/(?:channel/|@)([^/?]+)", url)
        if m:
            return m.group(1)
        m = re.search(r"@([^/?]+)", url)
        if m:
            return m.group(1)
    if platform == Platform.XIAOHONGSHU:
        m = re.search(r"/user/profile/([^/?]+)", url)
        if m:
            return m.group(1)
    return url  # 兜底用整个 URL 当 ID


async def load_creators_from_feishu(
    feishu_client,
    *,
    platform_filter: Platform | None = None,
    enabled_only: bool = True,
) -> dict[Platform, list[Creator]]:
    """从飞书 Base `creators` 表拉博主，按 platform 分组。

    Args:
        feishu_client: FeishuBaseClient 实例
        platform_filter: 只拉某个平台（默认全部）
        enabled_only: 只返回 enabled=True 的记录（默认）

    Returns:
        {Platform.YOUTUBE: [Creator, ...], Platform.BILIBILI: [...], ...}
    """
    records = await feishu_client.list_records("creators")
    logger.info("creators_fetched_from_feishu", total=len(records))

    by_platform: dict[Platform, list[Creator]] = {}
    skipped = 0

    for record in records:
        fields = _flatten_fields(record.get("fields", {}))

        # enabled 过滤
        if enabled_only:
            enabled_raw = fields.get("enabled") or fields.get("启用")
            if enabled_raw is False or (isinstance(enabled_raw, str) and enabled_raw.lower() in ("false", "0", "否", "关")):
                skipped += 1
                continue

        platform = _detect_platform(record)
        if platform is None:
            logger.warning("creator_platform_unrecognized", record_id=record.get("record_id"), fields=list(fields.keys()))
            skipped += 1
            continue

        if platform_filter and platform != platform_filter:
            continue

        # 提取必填字段
        name = fields.get("name") or fields.get("博主名") or fields.get("名称") or ""
        url = fields.get("url") or fields.get("主页链接") or fields.get("主页") or ""
        creator_id = fields.get("creator_id") or fields.get("博主ID") or fields.get("id") or _extract_id(platform, url)
        follower_count = fields.get("follower_count") or fields.get("粉丝数")
        tags = fields.get("tags") or fields.get("标签") or ""
        if isinstance(tags, str) and tags:
            tags_list = [t.strip() for t in tags.split(",") if t.strip()]
        else:
            tags_list = []

        if not name or not url or not creator_id:
            logger.warning("creator_skipped_missing_fields", record_id=record.get("record_id"), name=name, url=url)
            skipped += 1
            continue

        creator = Creator(
            platform=platform,
            creator_id=str(creator_id),
            name=name,
            url=url,
            follower_count=int(follower_count) if follower_count and str(follower_count).isdigit() else None,
            tags=tags_list,
            enabled=True,
            notes=str(fields.get("notes") or fields.get("备注") or ""),
        )
        by_platform.setdefault(platform, []).append(creator)

    logger.info(
        "creators_grouped",
        total=len(records),
        skipped=skipped,
        platforms={p.value: len(cs) for p, cs in by_platform.items()},
    )
    return by_platform


async def load_creators_for_platform(
    feishu_client,
    platform: Platform,
    **kwargs,
) -> list[Creator]:
    """便捷：只拉某个平台。"""
    grouped = await load_creators_from_feishu(feishu_client, platform_filter=platform, **kwargs)
    return grouped.get(platform, [])
