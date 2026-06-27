"""ASR 后端抽象层。

设计要点（基于威哥踩坑教训）：
- Apple Silicon + Python 3.13 + MPS 后端组合**会卡死**（whisper / mlx-whisper 都中招）
- 主后端跑 5 分钟无输出立即降级到兜底后端，**绝不要闷头等 30+ 分钟**
- 云端 fallback 优先 OpenAI Whisper（最稳），DashScope Qwen-ASR 作为备选（中文好）

支持的 5 个后端（ASRBackend enum）：
1. MLX_WHISPER    - macOS 本地（mlx-community/whisper-large-v3-turbo）
2. MLX_AUDIO      - macOS 本地（Qwen3-ASR via mlx-audio，**威哥有试过**）
3. OPENAI_WHISPER - 云端 OpenAI（$0.006/分钟，需 OPENAI_API_KEY）
4. DASHSCOPE_QWEN - 阿里百炼 Qwen-ASR（¥0.04/分钟，需 DASHSCOPE_API_KEY）
5. NONE           - 跳过转录（仅在不需要字幕时使用）
"""
from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, ClassVar

from ..core.config import ASRConfig
from ..core.exceptions import ASRBackendUnavailableError, TranscriptionError
from ..core.logging import get_logger
from ..core.models import ASRBackend

logger = get_logger(__name__)


class ASRResult:
    """ASR 转录结果。"""

    def __init__(self, text: str, segments: list[dict[str, Any]] | None = None, language: str | None = None) -> None:
        self.text = text
        self.segments = segments or []
        self.language = language

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text, "segments": self.segments, "language": self.language}


class ASRBackendImpl(ABC):
    """ASR 后端抽象基类。"""

    backend: ClassVar[ASRBackend]

    def __init__(self, asr_config: ASRConfig) -> None:
        self.config = asr_config
        self.log = get_logger(f"{__name__}.{self.backend.value}")

    @abstractmethod
    def is_available(self) -> tuple[bool, str]:
        """检查后端是否可用（依赖、API key、MPS 启动）。返回 (ok, reason)。"""

    @abstractmethod
    async def transcribe(self, audio_path: Path, *, model: str | None = None) -> ASRResult:
        """转录音频文件。"""


class MLXWhisperBackend(ASRBackendImpl):
    """macOS 本地 mlx-whisper 后端（MPS 加速）。

    ⚠️ Apple Silicon + Python 3.13 + MPS 兼容性警告：
    - 模型加载阶段可能 hang（卡 5 分钟无输出）
    - 超时保护：5 分钟无结果强制 raise TranscriptionError，让 fallback 接管
    """

    backend: ClassVar[ASRBackend] = ASRBackend.MLX_WHISPER

    def is_available(self) -> tuple[bool, str]:
        try:
            import mlx_whisper  # type: ignore[import-not-found]
        except ImportError:
            return False, "mlx-whisper 未安装（pip install hs-creator-crawler[mps]）"
        import platform
        if platform.system() != "Darwin":
            return False, "mlx-whisper 仅支持 macOS"
        return True, "ok"

    async def transcribe(self, audio_path: Path, *, model: str | None = None) -> ASRResult:
        if not audio_path.exists():
            raise TranscriptionError(f"音频文件不存在: {audio_path}", backend=self.backend.value)
        import mlx_whisper  # type: ignore[import-not-found]

        use_model = model or self.config.primary_model

        def _run() -> dict[str, Any]:
            return mlx_whisper.transcribe(  # type: ignore[attr-defined]
                str(audio_path),
                path_or_hf_repo=use_model,
                language="zh",
            )

        try:
            # 5 分钟硬超时（避免 MPS 卡死 hang）
            raw = await asyncio.wait_for(asyncio.to_thread(_run), timeout=300)
        except asyncio.TimeoutError as exc:
            raise TranscriptionError(
                "mlx-whisper 5 分钟无输出，疑似 MPS 卡死，请启用 fallback 后端",
                backend=self.backend.value,
                audio_path=str(audio_path),
            ) from exc
        except Exception as exc:
            raise TranscriptionError(str(exc), backend=self.backend.value, audio_path=str(audio_path)) from exc

        return ASRResult(
            text=(raw.get("text") or "").strip(),
            segments=raw.get("segments") or [],
            language=raw.get("language"),
        )


class OpenAIWhisperBackend(ASRBackendImpl):
    """云端 OpenAI Whisper 后端。"""

    backend: ClassVar[ASRBackend] = ASRBackend.OPENAI_WHISPER

    def is_available(self) -> tuple[bool, str]:
        if not self.config.openai_api_key:
            return False, "OPENAI_API_KEY 未配置"
        try:
            import openai  # type: ignore[import-not-found]
        except ImportError:
            return False, "openai 包未安装"
        return True, "ok"

    async def transcribe(self, audio_path: Path, *, model: str | None = None) -> ASRResult:
        if not audio_path.exists():
            raise TranscriptionError(f"音频文件不存在: {audio_path}", backend=self.backend.value)
        api_key = self.config.openai_api_key
        if not api_key:
            raise ASRBackendUnavailableError("OPENAI_API_KEY 未配置", backend=self.backend.value)

        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=api_key)
        use_model = model or self.config.fallback_model
        try:
            with open(audio_path, "rb") as fp:
                resp = await client.audio.transcriptions.create(
                    model=use_model,
                    file=fp,
                    language="zh",
                    response_format="verbose_json",
                    timeout=300,
                )
        except Exception as exc:
            raise TranscriptionError(str(exc), backend=self.backend.value, audio_path=str(audio_path)) from exc

        # resp 可能是 Pydantic model 或 dict
        text = getattr(resp, "text", None) or (resp.get("text") if isinstance(resp, dict) else "")
        segments = getattr(resp, "segments", None) or (resp.get("segments") if isinstance(resp, dict) else []) or []
        language = getattr(resp, "language", None) or (resp.get("language") if isinstance(resp, dict) else None)
        return ASRResult(text=text or "", segments=segments, language=language)


class DashScopeQwenBackend(ASRBackendImpl):
    """阿里百炼 Qwen-ASR 后端（中文识别最好）。"""

    backend: ClassVar[ASRBackend] = ASRBackend.DASHSCOPE_QWEN

    def is_available(self) -> tuple[bool, str]:
        if not self.config.dashscope_api_key:
            return False, "DASHSCOPE_API_KEY 未配置"
        try:
            import dashscope  # type: ignore[import-not-found]
        except ImportError:
            return False, "dashscope 包未安装"
        return True, "ok"

    async def transcribe(self, audio_path: Path, *, model: str | None = None) -> ASRResult:
        if not audio_path.exists():
            raise TranscriptionError(f"音频文件不存在: {audio_path}", backend=self.backend.value)
        api_key = self.config.dashscope_api_key
        if not api_key:
            raise ASRBackendUnavailableError("DASHSCOPE_API_KEY 未配置", backend=self.backend.value)

        import dashscope  # type: ignore[import-not-found]
        from dashscope.audio.asr import Recognition  # type: ignore[import-not-found]

        dashscope.api_key = api_key

        def _call_recognition() -> dict[str, Any]:
            recognition = Recognition(
                model="paraformer-v2" if not model else model,
                format="mp3",
                sample_rate=16000,
                language_hints=["zh", "en"],
                callback=None,
            )
            result = recognition.call(str(audio_path))
            return {"text": result.get_sentence() if hasattr(result, "get_sentence") else str(result)}

        try:
            raw = await asyncio.to_thread(_call_recognition)
        except Exception as exc:
            raise TranscriptionError(str(exc), backend=self.backend.value, audio_path=str(audio_path)) from exc

        return ASRResult(text=raw.get("text", "").strip(), segments=[], language="zh")


class NoOpBackend(ASRBackendImpl):
    """空实现（仅用于跳过转录的场景）。"""

    backend: ClassVar[ASRBackend] = ASRBackend.NONE

    def is_available(self) -> tuple[bool, str]:
        return True, "noop"

    async def transcribe(self, audio_path: Path, *, model: str | None = None) -> ASRResult:
        return ASRResult(text="")


# 后端注册表
_BACKENDS: dict[ASRBackend, type[ASRBackendImpl]] = {
    ASRBackend.MLX_WHISPER: MLXWhisperBackend,
    ASRBackend.OPENAI_WHISPER: OpenAIWhisperBackend,
    ASRBackend.DASHSCOPE_QWEN: DashScopeQwenBackend,
    ASRBackend.NONE: NoOpBackend,
}


def get_asr_backend(backend: ASRBackend, asr_config: ASRConfig) -> ASRBackendImpl:
    """按 ASRBackend enum 拿后端实例。"""
    cls = _BACKENDS.get(backend)
    if cls is None:
        raise ASRBackendUnavailableError(f"ASR 后端 {backend.value} 未实现", backend=backend.value)
    return cls(asr_config)


async def transcribe_with_fallback(
    audio_path: Path,
    asr_config: ASRConfig,
) -> ASRResult:
    """主后端失败自动 fallback（避免 MPS 卡死/网络故障阻塞整条 pipeline）。

    Returns:
        ASRResult（带 backend 信息）
    """
    started_at = time.time()
    primary = get_asr_backend(asr_config.primary, asr_config)

    ok, reason = primary.is_available()
    if ok:
        try:
            result = await primary.transcribe(audio_path)
            elapsed = time.time() - started_at
            logger.info(
                "asr_transcribed",
                backend=asr_config.primary.value,
                audio=str(audio_path),
                elapsed_seconds=round(elapsed, 2),
                chars=len(result.text),
            )
            return result
        except TranscriptionError as exc:
            logger.warning("asr_primary_failed", backend=asr_config.primary.value, error=str(exc))

    if asr_config.fallback is None:
        raise TranscriptionError(
            f"主 ASR 后端失败 ({reason})，且无 fallback",
            backend=asr_config.primary.value,
            audio_path=str(audio_path),
        )

    fallback = get_asr_backend(asr_config.fallback, asr_config)
    ok, reason = fallback.is_available()
    if not ok:
        raise ASRBackendUnavailableError(f"Fallback ASR 后端不可用: {reason}", backend=asr_config.fallback.value)

    result = await fallback.transcribe(audio_path)
    elapsed = time.time() - started_at
    logger.info(
        "asr_transcribed_via_fallback",
        backend=asr_config.fallback.value,
        audio=str(audio_path),
        elapsed_seconds=round(elapsed, 2),
        chars=len(result.text),
    )
    return result
