"""ASR 后端模块。"""

from .backends import ASRResult, get_asr_backend, transcribe_with_fallback

__all__ = ["ASRResult", "get_asr_backend", "transcribe_with_fallback"]
