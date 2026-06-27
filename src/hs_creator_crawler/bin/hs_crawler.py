"""hs-crawler CLI 入口（替代 dragon-hh 原 `download_all_platform_latest.py` 顶层入口）。

支持 3 个子命令：
- ingest         全平台采集（核心）
- health-check   平台连通性自检
- init-config    生成示例配置文件

用法：
    hs-crawler ingest --videos-per-creator 5 --transcribe --sync-to-feishu
    hs-crawler health-check
    hs-crawler init-config ~/.config/hs-creator-crawler/config.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from ..core import (
    DEFAULT_CONFIG_PATH,
    CreatorTrackerConfig,
    configure_logging,
    get_logger,
    write_example_config,
)
from ..core.exceptions import ConfigError
from ..platforms import PLATFORM_REGISTRY

logger = get_logger(__name__)


def _cmd_health_check(args: argparse.Namespace) -> int:
    """平台连通性自检（串行跑各平台，最快失败优先）。"""
    config = CreatorTrackerConfig.load(args.config)
    configure_logging(args.log_level, json_only=args.log_json)

    async def _check_all() -> dict[str, dict]:
        from ..platforms import get_crawler

        results: dict[str, dict] = {}
        for platform in config.enabled_platforms:
            try:
                crawler = get_crawler(platform, config)
            except Exception as exc:
                results[platform.value] = {"ok": False, "error": f"register_failed: {exc}"}
                continue
            try:
                results[platform.value] = await crawler.health_check()
            except Exception as exc:
                results[platform.value] = {"ok": False, "error": str(exc)}
        return results

    results = asyncio.run(_check_all())
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0 if all(r.get("ok") for r in results.values()) else 1


def _cmd_init_config(args: argparse.Namespace) -> int:
    """生成示例配置文件。"""
    target = Path(args.path) if args.path else DEFAULT_CONFIG_PATH
    if target.exists() and not args.force:
        print(f"配置已存在: {target}。--force 覆盖。", file=sys.stderr)
        return 2
    write_example_config(target)
    print(f"✅ 示例配置已写入: {target}")
    print(f"   请编辑后填入 HS_CC_FEISHU_APP_ID / HS_CC_FEISHU_APP_SECRET / OPENAI_API_KEY 等环境变量")
    print(f"   博主列表默认从 creators/{{platform}}.json 加载，飞书 Base 加载功能开发中")
    return 0


def _cmd_ingest(args: argparse.Namespace) -> int:
    """全平台采集。"""
    from ..pipelines.all_platforms import main as all_platforms_main

    argv = [
        "ingest",
        *(["--config", args.config] if args.config else []),
        *(["--creators-dir", args.creators_dir] if args.creators_dir else []),
        "--videos-per-creator", str(args.videos_per_creator),
        *(["--max-creators", str(args.max_creators)] if args.max_creators else []),
        *(["--no-skip-existing"] if args.no_skip_existing else []),
        *(["--dry-run"] if args.dry_run else []),
        *(["--stop-on-error"] if args.stop_on_error else []),
        "--transcribe" if args.transcribe else "--no-transcribe",
        "--sync-to-feishu" if args.sync_to_feishu else "--no-sync-to-feishu",
        *(["--from-feishu"] if getattr(args, "from_feishu", True) else ["--no-from-feishu"]),
        "--log-level", args.log_level,
        *(["--log-json"] if args.log_json else []),
    ]
    return all_platforms_main(argv)


def _cmd_list_platforms(args: argparse.Namespace) -> int:
    """列出已注册的平台实现。"""
    print("已注册平台（PLATFORM_REGISTRY）:")
    for p, cls in PLATFORM_REGISTRY.items():
        print(f"  - {p.value:12s}  {cls.__module__}.{cls.__name__}")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="hs-crawler", description="合盛创作者追踪 · 主流媒体博主主动监控")
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--log-json", action="store_true")

    sub = parser.add_subparsers(dest="command", required=True)

    p_ingest = sub.add_parser("ingest", help="全平台采集")
    p_ingest.add_argument("--config", help="配置文件路径")
    p_ingest.add_argument("--creators-dir", help="博主 JSON 目录")
    p_ingest.add_argument("--videos-per-creator", type=int, default=3)
    p_ingest.add_argument("--max-creators", type=int)
    p_ingest.add_argument("--transcribe", action=argparse.BooleanOptionalAction, default=True)
    p_ingest.add_argument("--sync-to-feishu", action=argparse.BooleanOptionalAction, default=True)
    p_ingest.add_argument("--no-skip-existing", action="store_true")
    p_ingest.add_argument("--dry-run", action="store_true")
    p_ingest.add_argument("--stop-on-error", action="store_true")
    p_ingest.add_argument(
        "--from-feishu",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="从飞书 Base `creators` 表拉博主（合盛体系集成；默认开，失败 fallback 到本地 JSON）",
    )

    p_health = sub.add_parser("health-check", help="平台连通性自检")
    p_health.add_argument("--config")

    p_init = sub.add_parser("init-config", help="生成示例配置文件")
    p_init.add_argument("path", nargs="?")
    p_init.add_argument("--force", action="store_true")

    p_list = sub.add_parser("list-platforms", help="列出已注册的平台")

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    configure_logging(args.log_level, json_only=args.log_json)

    try:
        if args.command == "ingest":
            return _cmd_ingest(args)
        if args.command == "health-check":
            return _cmd_health_check(args)
        if args.command == "init-config":
            return _cmd_init_config(args)
        if args.command == "list-platforms":
            return _cmd_list_platforms(args)
    except ConfigError as exc:
        print(f"配置错误: {exc}", file=sys.stderr)
        return 78
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        return 130

    return 0


if __name__ == "__main__":
    sys.exit(main())
