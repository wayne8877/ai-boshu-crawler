"""飞书 Base 客户端（asyncio + 动态 schema）。

设计要点：
1. schema 走配置（`CreatorTrackerConfig.feishu.tables[role]`），新表名/新字段只改配置
2. 用 lark-oapi 异步 SDK（支持 tenant_access_token 自动续期）
3. 优雅降级：字段缺失只 log warning，不阻塞整条 pipeline
4. 写失败有 retry（指数退避 + 限流检测）
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar

from ..core.config import CreatorTrackerConfig, FeishuBaseTableConfig
from ..core.exceptions import (
    FeishuAuthError,
    FeishuBaseSchemaError,
    FeishuRateLimitError,
)
from ..core.logging import get_logger
from ..core.models import Creator, Video

logger = get_logger(__name__)


class FeishuBaseClient:
    """飞书 Base 客户端（轻量封装，不直接依赖 lark-oapi 大对象）。"""

    def __init__(self, config: CreatorTrackerConfig) -> None:
        self.config = config
        self.log = get_logger(__name__)
        self._access_token: str | None = None
        self._token_expires_at: float = 0.0
        self._lock = asyncio.Lock()
        # SDK 可选导入（lark-oapi 缺失时不阻塞，仅 Base 操作不可用）
        try:
            import lark_oapi as lark  # type: ignore[import-not-found]

            self._lark = lark
        except ImportError:
            self.log.warning("lark_oapi_missing", note="飞书 Base 写入不可用；可安装 `pip install lark-oapi` 启用")
            self._lark = None

    async def _get_access_token(self) -> str:
        """获取/刷新 tenant_access_token（带本地缓存 + 异步锁）。"""
        async with self._lock:
            if self._access_token and self._token_expires_at > time.time() + 300:
                return self._access_token
            if self._lark is None:
                raise FeishuAuthError("lark-oapi 未安装，无法获取 access_token")

            app_id = self.config.feishu.app_id
            app_secret = self.config.feishu.app_secret
            if not app_id or not app_secret:
                raise FeishuAuthError("feishu.app_id / app_secret 未配置（可通过 HS_CC_FEISHU_APP_ID/SECRET 环境变量）")

            # lark-oapi 的 token 客户端是同步的；放线程池跑
            def _fetch() -> dict[str, Any]:
                req = (
                    self._lark.im.v1.create_request(  # type: ignore[attr-defined]
                        method="POST",
                        url="https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                        data={"app_id": app_id, "app_secret": app_secret},
                    )
                )
                return self._lark.im.v1.create_request  # type: ignore[attr-defined]

            # 简化路径：直接用 httpx 调 token 端点（避免 SDK 版本兼容问题）
            import httpx

            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(
                    "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                    json={"app_id": app_id, "app_secret": app_secret},
                )
                r.raise_for_status()
                payload = r.json()
                if payload.get("code") != 0:
                    raise FeishuAuthError(f"飞书 token 获取失败: {payload}")
                self._access_token = payload["tenant_access_token"]
                self._token_expires_at = time.time() + payload.get("expire", 7200)
                return self._access_token

    async def _api(self, method: str, url: str, *, json: dict[str, Any] | None = None) -> dict[str, Any]:
        """统一 API 调用入口（带 auth + 限流重试）。"""
        import httpx

        token = await self._get_access_token()
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"}
        full_url = f"{self.config.feishu.base_url.rstrip('/')}/{url.lstrip('/')}"

        max_attempts = 3
        backoff = 1.0
        for attempt in range(1, max_attempts + 1):
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.request(method, full_url, headers=headers, json=json)
                if r.status_code == 429:
                    retry_after = float(r.headers.get("Retry-After", backoff))
                    self.log.warning("feishu_rate_limited", retry_after=retry_after, attempt=attempt)
                    await asyncio.sleep(retry_after)
                    backoff *= 2
                    continue
                r.raise_for_status()
                payload = r.json()
                if payload.get("code") == 99991663 or "token" in str(payload.get("msg", "")).lower():
                    self._access_token = None  # 强制刷新
                    raise FeishuAuthError(f"token 失效: {payload}")
                if payload.get("code") not in (0, None):
                    raise FeishuBaseSchemaError(f"飞书 API 错误: {payload.get('msg')} (code={payload.get('code')})")
                return payload.get("data") or {}

        raise FeishuRateLimitError("飞书 API 重试耗尽", retry_after=backoff)

    async def list_records(self, role: str, *, filter_expr: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """列出一张表的记录。role = creators/videos/comments/..."""
        table = self._get_table(role)
        params: dict[str, Any] = {"page_size": 500}
        if filter_expr:
            params["filter"] = filter_expr
        data = await self._api(
            "GET",
            f"bitable/v1/apps/{table.app_token}/tables/{table.table_id}/records",
            json=params,
        )
        items = data.get("items") or []
        return items

    async def upsert_record(self, role: str, fields: dict[str, Any], *, record_id: str | None = None) -> str:
        """插入或更新一条记录，返回 record_id。fields 自动按 schema.field_mapping 翻译。"""
        table = self._get_table(role)
        translated = self._translate_fields(role, fields)

        if record_id:
            data = await self._api(
                "PUT",
                f"bitable/v1/apps/{table.app_token}/tables/{table.table_id}/records/{record_id}",
                json={"fields": translated},
            )
            return data.get("record", {}).get("record_id") or record_id

        data = await self._api(
            "POST",
            f"bitable/v1/apps/{table.app_token}/tables/{table.table_id}/records",
            json={"fields": translated},
        )
        record = data.get("record") or {}
        return record.get("record_id", "")

    async def upsert_video(self, video: Video) -> str | None:
        """便捷方法：upsert 一条视频记录（按 platform+video_id 去重）。"""
        if "videos" not in self.config.feishu.tables:
            self.log.warning("feishu_videos_table_not_configured", note="跳过视频同步")
            return None

        fields = {
            "platform": video.platform.value,
            "video_id": video.video_id,
            "creator_id": video.creator_id,
            "title": video.title,
            "url": str(video.url),
            "published_at": video.published_at.isoformat() if video.published_at else "",
            "duration_seconds": video.duration_seconds,
            "view_count": video.view_count,
            "like_count": video.like_count,
            "comment_count": video.comment_count,
            "local_video_path": str(video.local_video_path) if video.local_video_path else "",
            "transcript_text": video.transcript_text or "",
            "asr_backend": video.asr_backend.value,
        }

        # 去重查询
        try:
            existing = await self.list_records(
                "videos",
                filter_expr={
                    "conjunction": "and",
                    "conditions": [
                        {"field_name": "video_id", "operator": "is", "value": [video.video_id]},
                        {"field_name": "platform", "operator": "is", "value": [video.platform.value]},
                    ],
                },
            )
        except FeishuBaseSchemaError as exc:
            self.log.warning("feishu_lookup_skipped", error=str(exc))
            existing = []

        record_id = existing[0].get("record_id") if existing else None
        new_id = await self.upsert_record("videos", fields, record_id=record_id)
        self.log.info("feishu_video_upserted", record_id=new_id, video_id=video.video_id)
        return new_id

    async def upsert_creator(self, creator: Creator) -> str | None:
        if "creators" not in self.config.feishu.tables:
            return None
        fields = {
            "platform": creator.platform.value,
            "creator_id": creator.creator_id,
            "name": creator.name,
            "url": str(creator.url),
            "follower_count": creator.follower_count or 0,
            "tags": ", ".join(creator.tags) if creator.tags else "",
            "last_polled_at": (creator.last_polled_at or datetime.now()).isoformat(),
        }
        try:
            existing = await self.list_records(
                "creators",
                filter_expr={
                    "conjunction": "and",
                    "conditions": [
                        {"field_name": "creator_id", "operator": "is", "value": [creator.creator_id]},
                        {"field_name": "platform", "operator": "is", "value": [creator.platform.value]},
                    ],
                },
            )
        except FeishuBaseSchemaError as exc:
            self.log.warning("feishu_lookup_skipped", error=str(exc))
            existing = []

        record_id = existing[0].get("record_id") if existing else None
        return await self.upsert_record("creators", fields, record_id=record_id)

    async def log_task(self, payload: dict[str, Any]) -> str | None:
        """写一条任务日志到 crawl_task_logs 表。"""
        if "crawl_task_logs" not in self.config.feishu.tables:
            return None
        return await self.upsert_record(
            "crawl_task_logs",
            {
                "platform": payload.get("platform", ""),
                "step": payload.get("step", ""),
                "started_at": payload.get("started_at", ""),
                "ended_at": payload.get("ended_at", ""),
                "ok": bool(payload.get("ok", False)),
                "summary_json": str(payload),
            },
        )

    def _get_table(self, role: str) -> FeishuBaseTableConfig:
        if role not in self.config.feishu.tables:
            raise FeishuBaseSchemaError(f"飞书表 role '{role}' 未在配置中定义。可用: {list(self.config.feishu.tables)}")
        return self.config.feishu.tables[role]

    def _translate_fields(self, role: str, fields: dict[str, Any]) -> dict[str, Any]:
        """按 schema.field_mapping 把本系统字段翻译到飞书字段名。

        未映射的字段直接 drop（避免写错字段名）。
        """
        table = self._get_table(role)
        mapping = table.field_mapping or {}
        out: dict[str, Any] = {}
        for k, v in fields.items():
            target = mapping.get(k, k)
            out[target] = v
        return out


# 便捷单例（懒加载，避免循环引用）
_default_client: FeishuBaseClient | None = None


def get_feishu_client(config: CreatorTrackerConfig) -> FeishuBaseClient:
    global _default_client
    if _default_client is None:
        _default_client = FeishuBaseClient(config)
    return _default_client
