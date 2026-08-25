"""synthesize_voice 内置工具。"""

from base64 import b64encode
from typing import Any, Optional

from src.chat.tts_system import tts_manager
from src.common.logger import get_logger
from src.config.config import global_config
from src.core.tooling import ToolExecutionContext, ToolExecutionResult, ToolInvocation, ToolSpec
from src.services import send_service

from .context import BuiltinToolRuntimeContext

logger = get_logger("maisaka_builtin_synthesize_voice")


def get_tool_spec() -> ToolSpec:
    """获取 synthesize_voice 工具声明。"""

    return ToolSpec(
        name="synthesize_voice",
        description=(
            "把一段文本合成为语音消息并发送给用户，适合朗读诗句、祝福语、角色台词等"
            "需要用声音表达的内容。每次调用发送一条语音；普通文字回复不要使用本工具。"
        ),
        parameters_schema={
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "要合成为语音的文本内容。",
                },
            },
            "required": ["text"],
        },
        provider_name="maisaka_builtin",
        provider_type="builtin",
    )


def _collect_missing_provider_fields() -> list[str]:
    """检查当前 provider 下必配后端参数是否齐全。

    必配项与各后端实现的真实约束保持一致（openai_compat 的 api_key 可选，
    本地免密 TTS 服务属合法部署）。
    """

    provider = str(global_config.tts.provider or "").strip()

    def _format_missing(fields: tuple[tuple[str, Any], ...]) -> list[str]:
        return [
            f"tts.{provider}.{field_name}"
            for field_name, field_value in fields
            if not str(field_value or "").strip()
        ]

    if provider == "openai_compat":
        backend_cfg = global_config.tts.openai_compat
        return _format_missing((("base_url", backend_cfg.base_url),))
    if provider == "gpt_sovits":
        backend_cfg = global_config.tts.gpt_sovits
        return _format_missing(
            (("ref_audio_path", backend_cfg.ref_audio_path), ("prompt_text", backend_cfg.prompt_text))
        )
    if provider == "fish_speech":
        backend_cfg = global_config.tts.fish_speech
        return _format_missing((("reference_id", backend_cfg.reference_id),))
    return []


async def handle_tool(
    tool_ctx: BuiltinToolRuntimeContext,
    invocation: ToolInvocation,
    context: Optional[ToolExecutionContext] = None,
) -> ToolExecutionResult:
    """执行语音合成内置工具。"""

    structured_content: dict[str, Any] = {
        "success": False,
        "provider": str(global_config.tts.provider or ""),
        "stream_id": invocation.stream_id or (context.stream_id if context is not None else ""),
    }

    if not bool(global_config.tts.enable):
        return tool_ctx.build_failure_result(
            invocation.tool_name,
            "语音合成组件未启用：请在配置中开启 tts.enable 后再使用该工具。",
            structured_content=structured_content,
        )

    raw_text = str(invocation.arguments.get("text") or "").strip()
    if not raw_text:
        return tool_ctx.build_failure_result(
            invocation.tool_name,
            "缺少必填参数 text：请提供要合成为语音的文本。",
            structured_content=structured_content,
        )

    missing_fields = _collect_missing_provider_fields()
    if missing_fields:
        return tool_ctx.build_failure_result(
            invocation.tool_name,
            f"TTS 后端参数缺失：{'、'.join(missing_fields)}，请先在配置文件中补全后再调用。",
            structured_content=structured_content,
        )

    target_stream_id = str(structured_content["stream_id"])
    if not target_stream_id:
        return tool_ctx.build_failure_result(
            invocation.tool_name,
            "无法确定目标会话：invocation 与执行上下文均缺少 stream_id。",
            structured_content=structured_content,
        )

    try:
        audio_bytes, audio_format = await tts_manager.synthesize(raw_text)
    except Exception as exc:
        logger.exception(f"语音合成失败: provider={global_config.tts.provider}")
        return tool_ctx.build_failure_result(
            invocation.tool_name,
            f"语音合成失败：{exc.__class__.__name__}: {exc}",
            structured_content=structured_content,
        )

    structured_content["format"] = audio_format
    structured_content["audio_bytes"] = len(audio_bytes)

    # 音频以 base64 直接走发送服务，不经 content_items 回传，避免大段数据进入对话历史。
    try:
        sent_message = await send_service.custom_to_stream_with_message(
            message_type="voice",
            content=b64encode(audio_bytes).decode("utf-8"),
            stream_id=target_stream_id,
            storage_message=True,
            sync_to_maisaka_history=True,
            maisaka_source_kind="synthesize_voice",
        )
    except Exception as exc:
        logger.exception(f"语音消息发送失败: stream_id={target_stream_id}")
        return tool_ctx.build_failure_result(
            invocation.tool_name,
            f"语音消息发送失败：{exc.__class__.__name__}: {exc}",
            structured_content=structured_content,
        )

    if sent_message is None:
        return tool_ctx.build_failure_result(
            invocation.tool_name,
            f"语音消息发送失败：目标会话 {target_stream_id} 未返回已发送消息。",
            structured_content=structured_content,
        )

    structured_content["success"] = True
    return tool_ctx.build_success_result(
        invocation.tool_name,
        f"已将文本合成为语音（{audio_format} 格式，{len(audio_bytes)} 字节）并发送给用户。",
        structured_content=structured_content,
    )
