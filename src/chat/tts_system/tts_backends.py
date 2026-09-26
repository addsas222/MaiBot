"""TTS 后端实现。

三个后端统一暴露 ``async def xxx_synthesize(text, cfg=None, timeout=None) -> bytes``，
返回 wav/mp3 等音频原始字节；任何错误直接抛出，不做兜底。
所有请求走共享的 ``get_main_http_client()``。
"""

from typing import Any, Optional

import httpx

from src.common.http_client import get_main_http_client
from src.common.logger import get_logger
from src.config.config import global_config
from src.config.official_configs import (
    FishSpeechTtsBackend,
    GptSovitsTtsBackend,
    OpenAICompatTtsBackend,
)

logger = get_logger("tts_system")


def _resolve_timeout(timeout: Optional[float]) -> httpx.Timeout:
    """把秒数超时转换为单次请求的 httpx.Timeout。"""

    seconds = float(timeout) if timeout is not None else float(global_config.tts.timeout_seconds)
    return httpx.Timeout(seconds)


def _normalize_base_url(base_url: str, backend_label: str) -> str:
    """去除 base_url 末尾斜杠并校验非空。"""

    normalized = str(base_url or "").strip().rstrip("/")
    if not normalized:
        raise ValueError(f"{backend_label} 后端 base_url 未配置，请在 tts 配置中填写服务地址。")
    return normalized


def _missing_fields(fields: tuple[tuple[str, Any], ...]) -> list[str]:
    """返回取值为空白的字段名列表。"""
    return [field_name for field_name, field_value in fields if not str(field_value or "").strip()]


def missing_config_fields(provider: str) -> list[str]:
    """返回指定 TTS provider 下缺失的必配后端字段名，齐全时返回空列表。

    必配项与各后端实现的真实约束保持一致（openai_compat 的 api_key 可选，
    本地免密 TTS 服务属合法部署）；base_url 由 ``_normalize_base_url`` 在请求前
    校验，故不在本清单内。provider 无法识别时返回空列表，交由分发处报未知 provider。
    """
    tts_cfg = global_config.tts
    if provider == "openai_compat":
        return _missing_fields((("base_url", tts_cfg.openai_compat.base_url),))
    if provider == "gpt_sovits":
        backend_cfg = tts_cfg.gpt_sovits
        return _missing_fields(
            (("ref_audio_path", backend_cfg.ref_audio_path), ("prompt_text", backend_cfg.prompt_text))
        )
    if provider == "fish_speech":
        return _missing_fields((("reference_id", tts_cfg.fish_speech.reference_id),))
    return []


async def openai_compat_synthesize(
    text: str,
    cfg: Optional[OpenAICompatTtsBackend] = None,
    timeout: Optional[float] = None,
) -> bytes:
    """调用 OpenAI 兼容 /audio/speech 接口合成语音。

    Args:
        text: 待合成文本。
        cfg: 后端配置；缺省时读取 ``global_config.tts.openai_compat``。
        timeout: 单次请求超时秒数；缺省时读取 ``global_config.tts.timeout_seconds``。

    Returns:
        bytes: 音频原始字节（wav）。

    Raises:
        ValueError: base_url 未配置。
        httpx.HTTPStatusError: 服务返回非 2xx 状态码。
    """

    backend_cfg = cfg if cfg is not None else global_config.tts.openai_compat
    url = f"{_normalize_base_url(backend_cfg.base_url, 'OpenAI 兼容 TTS')}/audio/speech"
    headers = {"Authorization": f"Bearer {backend_cfg.api_key}"} if backend_cfg.api_key else {}
    payload = {
        "model": backend_cfg.model,
        "voice": backend_cfg.voice,
        "input": text,
        "speed": backend_cfg.speed,
        "response_format": "wav",
    }
    response = await get_main_http_client().post(
        url,
        json=payload,
        headers=headers,
        timeout=_resolve_timeout(timeout),
    )
    response.raise_for_status()
    content_type = response.headers.get("content-type", "").strip().lower()
    if not (content_type.startswith("audio/") or content_type == "application/octet-stream"):
        logger.warning(
            f"OpenAI 兼容 TTS 返回了非音频 Content-Type: {content_type or '缺失'}，仍按原始字节处理。"
        )
    return response.content


async def gpt_sovits_synthesize(
    text: str,
    cfg: Optional[GptSovitsTtsBackend] = None,
    timeout: Optional[float] = None,
) -> bytes:
    """调用 GPT-SoVITS api_v2 /tts 接口合成语音。

    Args:
        text: 待合成文本。
        cfg: 后端配置；缺省时读取 ``global_config.tts.gpt_sovits``。
        timeout: 单次请求超时秒数；缺省时读取 ``global_config.tts.timeout_seconds``。

    Returns:
        bytes: 音频原始字节（wav 流）。

    Raises:
        ValueError: ref_audio_path / prompt_text 等必配参数缺失。
        httpx.HTTPStatusError: 服务返回非 2xx 状态码。
    """

    backend_cfg = cfg if cfg is not None else global_config.tts.gpt_sovits
    missing_fields = [
        field_name
        for field_name, field_value in (
            ("ref_audio_path", backend_cfg.ref_audio_path),
            ("prompt_text", backend_cfg.prompt_text),
        )
        if not str(field_value or "").strip()
    ]
    if missing_fields:
        raise ValueError(
            f"GPT-SoVITS 后端缺少必配参数：{'、'.join(missing_fields)}；"
            "请按 api_v2 的要求在 tts.gpt_sovits 配置中填写参考音频路径与对应提示文本。"
        )
    url = f"{_normalize_base_url(backend_cfg.base_url, 'GPT-SoVITS')}/tts"
    payload = {
        "text": text,
        "text_lang": backend_cfg.text_lang,
        "ref_audio_path": backend_cfg.ref_audio_path,
        "prompt_text": backend_cfg.prompt_text,
        "prompt_lang": backend_cfg.prompt_lang,
        "aux_ref_audio_paths": list(backend_cfg.aux_ref_audio_paths),
        "streaming": False,
        "media_type": "wav",
    }
    response = await get_main_http_client().post(url, json=payload, timeout=_resolve_timeout(timeout))
    response.raise_for_status()
    return response.content


async def fish_speech_synthesize(
    text: str,
    cfg: Optional[FishSpeechTtsBackend] = None,
    timeout: Optional[float] = None,
) -> bytes:
    """调用 fish-speech 自托管服务 /v1/tts 接口合成语音。

    Args:
        text: 待合成文本。
        cfg: 后端配置；缺省时读取 ``global_config.tts.fish_speech``。
        timeout: 单次请求超时秒数；缺省时读取 ``global_config.tts.timeout_seconds``。

    Returns:
        bytes: 音频原始字节。

    Raises:
        ValueError: reference_id 未配置且无替代参考音频。
        httpx.HTTPStatusError: 服务返回非 2xx 状态码。
    """

    backend_cfg = cfg if cfg is not None else global_config.tts.fish_speech
    reference_id = str(backend_cfg.reference_id or "").strip()
    if not reference_id:
        # 当前实现未提供 reference_audio 上传替代通道，音色 ID 为唯一指定方式。
        raise ValueError(
            "Fish Speech 后端未配置音色：请在 tts.fish_speech.reference_id 填写参考音色 ID。"
        )
    url = f"{_normalize_base_url(backend_cfg.base_url, 'Fish Speech')}/v1/tts"
    payload = {
        "text": text,
        "reference_id": reference_id,
        "format": backend_cfg.format,
        "chunk_length": 200,
        "normalize": True,
    }
    response = await get_main_http_client().post(url, json=payload, timeout=_resolve_timeout(timeout))
    response.raise_for_status()
    return response.content
