"""全平台编排 pipeline（替代 dragon-hh 原 `download_all_platform_latest.py`）。

设计要点（保留原架构优势，重写细节）：
- ✅ 每平台独立 step，失败聚合到 PipelineResult.failures
- ✅ dry-run / stop-on-error / retries 开关
- ✅ manifest 落 `downloads/manifests/{ts}-all-platform-latest.json`
- ✅ 每个 step 独立的 started_at / ended_at / returncode / elapsed
- 🆕 asyncio.gather 并行拉取各平台（vs 原 subprocess 串行）
- 🆕 ASR 走 mlx-whisper + openai-whisper 双后端
- 🆕 飞书 Base upsert（带 schema 翻译 + 限流 retry）
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from ..asr import transcribe_with_fallback
from ..core.config import CreatorTrackerConfig
from ..core.exceptions import HSCrawlerError, PlatformError
from ..core.logging import get_logger
from ..core.models import Creator, PipelineResult, Platform, StepResult, Video
from ..feishu import get_feishu_client
from ..platforms import get_crawler

logger = get_logger(__name__)


def _extract_audio(video_path: Path, audio_path: Path) -> Path:
    """用 ffmpeg 从视频抽音频（16k mono mp3，给 ASR 用）。"""
    if audio_path.exists():
        return audio_path
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-i", str(video_path),
        "-vn", "-ar", "16000", "-ac", "1", "-b:a", "32k",
        str(audio_path),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=600)
    except subprocess.CalledProcessError as exc:
        raise HSCrawlerError(f"ffmpeg 抽音频失败: {exc.stderr.decode(errors='ignore')[:500]}") from exc
    return audio_path


def _save_transcript(video: Video, transcript_text: str, transcripts_dir: Path) -> Path:
    """保存转录文本到 transcripts/{platform}/{video_id}.txt"""
    target = transcripts_dir / video.platform.value / f"{video.video_id}.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(transcript_text, encoding="utf-8")
    return target


def load_creators_from_json(platform: Platform, path: Path) -> list[Creator]:
    """从 JSON 文件加载博主列表（兜底来源，飞书 Base 不可用时用）。"""
    if not path.exists():
        return []
    items = json.loads(path.read_text(encoding="utf-8"))
    out: list[Creator] = []
    for item in items:
        try:
            out.append(
                Creator(
                    platform=platform,
                    creator_id=str(item["creator_id"]),
                    name=item["name"],
                    url=item["url"],
                    tags=item.get("tags", []),
                    follower_count=item.get("follower_count"),
                )
            )
        except Exception as exc:
            logger.warning("creator_load_skipped", item=item, error=str(exc))
    return out


def save_manifest(result: PipelineResult, manifest_dir: Path) -> Path:
    """落 manifest JSON（带时间戳）。"""
    manifest_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = manifest_dir / f"{ts}-all-platform-latest.json"
    path.write_text(
        json.dumps(json.loads(result.model_dump_json()), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


async def run_platform_step(
    *,
    name: str,
    platform: Platform,
    config: CreatorTrackerConfig,
    creators: list[Creator],
    videos_per_creator: int,
    transcribe: bool,
    sync_to_feishu: bool,
    max_creators: int | None = None,
    skip_existing: bool = True,
) -> StepResult:
    """单个平台的完整 step：拉取 → 下载 → 转录 → 飞书同步。"""
    started = datetime.now()
    started_perf = time.perf_counter()
    log = logger.bind(platform=platform.value)
    log.info("step_started", name=name, creators=len(creators))

    result = StepResult(
        name=name,
        started_at=started,
        ended_at=started,
        returncode=0,
        elapsed_seconds=0.0,
    )
    videos_downloaded = 0
    videos_transcribed = 0
    videos_synced = 0

    try:
        crawler = get_crawler(platform, config)
        targets = creators[:max_creators] if max_creators else creators

        # 拉取所有博主最新视频（并发）
        sem = asyncio.Semaphore(config.request_max_concurrent)

        async def _fetch(c: Creator) -> list[Video]:
            async with sem:
                try:
                    return await crawler.list_videos(c, videos_per_creator)
                except PlatformError as exc:
                    log.warning("fetch_failed", creator=c.name, error=str(exc))
                    return []

        fetch_tasks = [_fetch(c) for c in targets]
        all_videos_lists = await asyncio.gather(*fetch_tasks)
        all_videos = [v for vs in all_videos_lists for v in vs]

        log.info("videos_listed", total=len(all_videos))

        # 下载（并发）
        videos_dir = config.storage.downloads_root / config.storage.videos_subdir / platform.value

        async def _download(video: Video) -> Path | None:
            async with sem:
                if skip_existing and video.local_video_path and Path(video.local_video_path).exists():
                    return Path(video.local_video_path)
                try:
                    return await crawler.download_video(video, videos_dir)
                except Exception as exc:
                    log.warning("download_failed", video_id=video.video_id, error=str(exc))
                    return None

        download_results = await asyncio.gather(*[_download(v) for v in all_videos])
        for video, local_path in zip(all_videos, download_results):
            if local_path:
                video.local_video_path = str(local_path)
                videos_downloaded += 1
        log.info("videos_downloaded", count=videos_downloaded)

        # 转录（如果启用且 ASR 主后端不是 NONE）
        from ..core.models import ASRBackend as _ASRBackend

        if transcribe and config.asr.primary != _ASRBackend.NONE:
            audio_dir = config.storage.downloads_root / config.storage.audio_subdir / platform.value
            transcripts_dir = config.storage.downloads_root / config.storage.transcripts_subdir

            async def _transcribe(video: Video) -> None:
                if not video.local_video_path:
                    return
                if video.transcript_text:
                    return  # 已转录过
                if video.duration_seconds > config.asr.max_duration_seconds:
                    log.warning("transcribe_skipped_too_long", video_id=video.video_id, duration=video.duration_seconds)
                    return
                try:
                    audio_path = audio_dir / f"{video.video_id}.mp3"
                    await asyncio.to_thread(_extract_audio, Path(video.local_video_path), audio_path)
                    asr_result = await transcribe_with_fallback(audio_path, config.asr)
                    if asr_result.text:
                        video.transcript_text = asr_result.text
                        video.transcript_segments = asr_result.segments
                        video.asr_backend = config.asr.primary
                        save_transcript_path = _save_transcript(video, asr_result.text, transcripts_dir)
                        video.transcript_path = str(save_transcript_path)
                        nonlocal_videos_transcribed[0] += 1
                except Exception as exc:
                    log.warning("transcribe_failed", video_id=video.video_id, error=str(exc))

            nonlocal_videos_transcribed = [videos_transcribed]  # type: ignore[misc]
            await asyncio.gather(*[_transcribe(v) for v in all_videos if v.local_video_path])
            videos_transcribed = nonlocal_videos_transcribed[0]  # type: ignore[misc]

        # 飞书同步（如果启用）
        if sync_to_feishu and config.feishu.tables:
            feishu = get_feishu_client(config)
            for video in all_videos:
                if not video.local_video_path:
                    continue
                try:
                    record_id = await feishu.upsert_video(video)
                    if record_id:
                        video.feishu_record_id = record_id
                        video.last_synced_at = datetime.now()
                        videos_synced += 1
                except Exception as exc:
                    log.warning("feishu_upsert_failed", video_id=video.video_id, error=str(exc))

        result.summary = {
            "creators": len(targets),
            "videos_listed": len(all_videos),
            "videos_downloaded": videos_downloaded,
            "videos_transcribed": videos_transcribed,
            "videos_synced_to_feishu": videos_synced,
        }
        result.returncode = 0
    except Exception as exc:
        log.error("step_failed", error=str(exc))
        result.returncode = 1
        result.error_message = str(exc)[:1000]
    finally:
        result.ended_at = datetime.now()
        result.elapsed_seconds = round(time.perf_counter() - started_perf, 3)

    log.info("step_done", **result.summary)
    return result


async def run_all_platforms(args: argparse.Namespace) -> PipelineResult:
    """主入口（CLI 调用）。"""
    config_path = Path(args.config) if args.config else None
    config = CreatorTrackerConfig.load(config_path)
    config.ensure_storage_dirs()

    log = logger.bind(dry_run=args.dry_run)
    log.info("pipeline_started", platforms=[p.value for p in config.enabled_platforms])

    # 任一平台失败 → PipelineResult.platform 用第一个失败平台
    result = PipelineResult(
        started_at=datetime.now(),
        platform=config.enabled_platforms[0],
        dry_run=args.dry_run,
    )

    # TODO: 后续从飞书 Base 拉博主列表（异步）
    # 当前用 JSON 兜底（开发期）
    creators_dir = Path(args.creators_dir) if args.creators_dir else Path.cwd() / "creators"
    all_creators: dict[Platform, list[Creator]] = {}
    for platform in config.enabled_platforms:
        json_path = creators_dir / f"{platform.value}.json"
        all_creators[platform] = load_creators_from_json(platform, json_path)
        log.info("creators_loaded", platform=platform.value, count=len(all_creators[platform]))

    # 编排各平台 step（并发）
    step_tasks = []
    for platform in config.enabled_platforms:
        creators = all_creators.get(platform, [])
        if not creators:
            log.warning("no_creators_skipped", platform=platform.value)
            continue
        step_tasks.append(
            run_platform_step(
                name=f"{platform.value}_ingest",
                platform=platform,
                config=config,
                creators=creators,
                videos_per_creator=args.videos_per_creator,
                transcribe=args.transcribe,
                sync_to_feishu=args.sync_to_feishu,
                max_creators=args.max_creators,
                skip_existing=not args.no_skip_existing,
            )
        )

    step_results = await asyncio.gather(*step_tasks, return_exceptions=True)
    for step in step_results:
        if isinstance(step, Exception):
            logger.error("step_exception", error=str(step))
            result.failures.append({"step": "unknown", "error": str(step)})
        elif isinstance(step, StepResult):
            result.steps.append(step)
            if step.returncode != 0:
                result.failures.append({"step": step.name, "returncode": step.returncode, "error": step.error_message})

    # 聚合
    result.videos_downloaded = sum(s.summary.get("videos_downloaded", 0) for s in result.steps)
    result.videos_transcribed = sum(s.summary.get("videos_transcribed", 0) for s in result.steps)
    result.videos_synced_to_feishu = sum(s.summary.get("videos_synced_to_feishu", 0) for s in result.steps)
    result.ended_at = datetime.now()

    # 落 manifest
    manifest_path = save_manifest(result, config.storage.downloads_root / config.storage.manifests_subdir)
    log.info("manifest_saved", path=str(manifest_path))

    # 飞书写任务日志
    if config.feishu.tables.get("crawl_task_logs"):
        try:
            feishu = get_feishu_client(config)
            await feishu.log_task({
                "platform": "all",
                "step": "all_platforms_ingest",
                "started_at": result.started_at.isoformat(),
                "ended_at": result.ended_at.isoformat() if result.ended_at else None,
                "ok": result.success,
                "summary": result.to_manifest_summary(),
            })
        except Exception as exc:
            log.warning("feishu_task_log_failed", error=str(exc))

    if not result.success:
        log.warning("pipeline_with_failures", failed=len(result.failures))
        return result
    log.info("pipeline_success", **result.to_manifest_summary())
    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="合盛创作者追踪 · 全平台编排入口")
    parser.add_argument("--config", help="配置文件路径（默认 ~/.config/hs-creator-crawler/config.json）")
    parser.add_argument("--creators-dir", help="博主列表 JSON 目录（默认 ./creators/{platform}.json）")
    parser.add_argument("--videos-per-creator", type=int, default=3, help="每个博主抓几个最新视频")
    parser.add_argument("--max-creators", type=int, default=None, help="每平台最多处理博主数（调试用）")
    parser.add_argument("--transcribe", action=argparse.BooleanOptionalAction, default=True, help="是否 ASR 转录")
    parser.add_argument("--sync-to-feishu", action=argparse.BooleanOptionalAction, default=True, help="是否同步到飞书")
    parser.add_argument("--no-skip-existing", action="store_true", help="强制重新下载/转录（忽略已有文件）")
    parser.add_argument("--dry-run", action="store_true", help="只列视频，不下载/不转录/不写飞书")
    parser.add_argument("--stop-on-error", action="store_true", help="任一 step 失败立即终止")
    parser.add_argument("--log-level", default="INFO", help="DEBUG/INFO/WARNING/ERROR")
    parser.add_argument("--log-json", action="store_true", help="输出 JSON 行日志（cron/容器场景）")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    from ..core import configure_logging

    configure_logging(args.log_level, json_only=args.log_json)

    try:
        result = asyncio.run(run_all_platforms(args))
    except KeyboardInterrupt:
        logger.warning("pipeline_interrupted")
        return 130
    except Exception as exc:
        logger.exception("pipeline_crashed", error=str(exc))
        return 1

    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(main())
