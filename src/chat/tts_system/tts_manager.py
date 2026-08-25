"""TTS 管理器：文本截断与后端分发。"""

from src.common.logger import get_logger
from src.config.config import global_config

from .tts_backends import fish_speech_synthesize, gpt_sovits_synthesize, openai_compat_synthesize

logger = get_logger("tts_system")


class TtsManager:
    """统一语音合成入口。

    负责按 ``global_config.tts.provider`` 分发到具体后端，并把文本截断到
    ``global_config.tts.max_text_length``；后端异常原样向上抛出。
    """

    async def synthesize(self, text: str) -> tuple[bytes, str]:
        """合成语音。

        Args:
            text: 待合成文本；超长部分按配置截断。

        Returns:
            tuple[bytes, str]: 音频原始字节与格式后缀（如 ``wav``）。

        Raises:
            ValueError: 文本为空，或 provider 配置无法识别。
            Exception: 后端请求错误原样抛出。
        """

        cleaned_text = str(text or "").strip()
        if not cleaned_text:
            raise ValueError("待合成的文本为空，无法进行语音合成。")

        tts_config = global_config.tts
        max_length = int(tts_config.max_text_length)
        truncated_text = cleaned_text[:max_length]
        if len(truncated_text) < len(cleaned_text):
            logger.info(
                f"待合成文本长度 {len(cleaned_text)} 超出上限 {max_length}，已截断。"
            )

        provider = str(tts_config.provider or "").strip()
        if provider == "openai_compat":
            return await openai_compat_synthesize(truncated_text), "wav"
        if provider == "gpt_sovits":
            return await gpt_sovits_synthesize(truncated_text), "wav"
        if provider == "fish_speech":
            audio_format = str(global_config.tts.fish_speech.format or "").strip().lstrip(".").lower()
            return (
                await fish_speech_synthesize(truncated_text),
                audio_format or "wav",
            )
        raise ValueError(f"未知的 TTS provider: {provider!r}，可选值：openai_compat / gpt_sovits / fish_speech。")


tts_manager = TtsManager()
