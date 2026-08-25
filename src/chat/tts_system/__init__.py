"""语音合成系统（TTS）入口。

导出 ``tts_manager`` 单例供内置工具等模块统一调用。
"""

from .tts_backends import fish_speech_synthesize, gpt_sovits_synthesize, openai_compat_synthesize
from .tts_manager import TtsManager, tts_manager

__all__ = [
    "TtsManager",
    "fish_speech_synthesize",
    "gpt_sovits_synthesize",
    "openai_compat_synthesize",
    "tts_manager",
]
