"""图片生成内置动作。"""

from base64 import b64encode
from time import monotonic
from typing import Any, Optional

from src.chat.drawing_system import drawing_manager
from src.common.logger import get_logger
from src.config.config import global_config
from src.core.tooling import ToolExecutionContext, ToolExecutionResult, ToolInvocation, ToolSpec
from src.services import send_service

from .context import BuiltinToolRuntimeContext

logger = get_logger("maisaka_builtin_generate_image")


def _read_png_dimensions(image_bytes: bytes) -> tuple[int, int] | None:
    """从 PNG 二进制的 IHDR 块读取像素尺寸，无法解析时返回 None。"""
    if len(image_bytes) < 24 or image_bytes[:8] != b"\x89PNG\r\n\x1a\n" or image_bytes[12:16] != b"IHDR":
        return None
    width = int.from_bytes(image_bytes[16:20], "big")
    height = int.from_bytes(image_bytes[20:24], "big")
    return width, height


def _is_backend_configured() -> bool:
    """检查当前绘图 provider 的必要后端参数是否已填写。"""
    image_cfg = global_config.image_generation
    provider = str(image_cfg.provider or "").strip()
    if provider == "openai_compat":
        backend_cfg = image_cfg.openai_compat
        return bool(str(backend_cfg.base_url or "").strip()) and bool(str(backend_cfg.model or "").strip())
    if provider == "comfyui":
        return bool(str(image_cfg.comfyui.workflow_path or "").strip())
    if provider == "sd_webui":
        return bool(str(image_cfg.sd_webui.base_url or "").strip())
    return False


def get_tool_spec() -> ToolSpec:
    """获取图片生成工具声明。"""
    return ToolSpec(
        name="generate_image",
        description=(
            "根据文字描述现场生成一张图片并发送给用户。当用户要求画画、画一张图、"
            "生成图片、画个插画等创作类请求时使用。prompt 为画面内容描述（必填），"
            "negative_prompt 为不希望出现的元素描述（可选）。"
        ),
        parameters_schema={
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "画面内容的详细描述，越具体效果越好。",
                },
                "negative_prompt": {
                    "type": "string",
                    "description": "负向提示词，描述画面中不希望出现的元素。",
                    "default": "",
                },
            },
            "required": ["prompt"],
        },
        provider_name="maisaka_builtin",
        provider_type="builtin",
    )


async def handle_tool(
    tool_ctx: BuiltinToolRuntimeContext,
    invocation: ToolInvocation,
    context: Optional[ToolExecutionContext] = None,
) -> ToolExecutionResult:
    """执行图片生成内置动作：调用绘图后端落盘后走既有发送管线发给用户。"""

    del context
    arguments = dict(invocation.arguments or {})
    prompt = str(arguments.get("prompt") or "").strip()
    negative_prompt = str(arguments.get("negative_prompt") or "").strip()
    structured_content: dict[str, Any] = {
        "success": False,
        "stream_id": tool_ctx.runtime.session_id,
    }

    image_cfg = global_config.image_generation
    if not image_cfg.enable or not _is_backend_configured():
        return tool_ctx.build_failure_result(
            invocation.tool_name,
            "绘图功能未启用或当前后端参数未配置完整，无法生成图片。",
            structured_content=structured_content,
        )
    if not prompt:
        return tool_ctx.build_failure_result(
            invocation.tool_name,
            "需要提供非空的 prompt 画面描述。",
            structured_content=structured_content,
        )

    started_at = monotonic()
    try:
        stored_path, image_bytes = await drawing_manager.generate_and_save(prompt, negative_prompt)
    except Exception as exc:
        logger.exception(f"{tool_ctx.runtime.log_prefix} 生成图片失败: {exc}")
        return tool_ctx.build_failure_result(
            invocation.tool_name,
            f"生成图片失败：{exc}",
            structured_content=structured_content,
        )

    elapsed_seconds = monotonic() - started_at
    structured_content.update(
        {
            "stored_path": stored_path,
            "size_bytes": len(image_bytes),
            "elapsed_seconds": round(elapsed_seconds, 2),
        }
    )

    success = await send_service.image_to_stream(
        image_base64=b64encode(image_bytes).decode("utf-8"),
        stream_id=tool_ctx.runtime.session_id,
        sync_to_maisaka_history=True,
        maisaka_source_kind="generate_image",
    )
    if not success:
        return tool_ctx.build_failure_result(
            invocation.tool_name,
            f"图片已生成并保存到 {stored_path}，但发送到会话失败。",
            structured_content=structured_content,
        )

    dimensions = _read_png_dimensions(image_bytes)
    size_label = f"{dimensions[0]}x{dimensions[1]}" if dimensions is not None else f"{len(image_bytes)} 字节"
    structured_content["success"] = True
    confirmation = (
        f"已生成并发送图片：{stored_path}（尺寸 {size_label}，共 {len(image_bytes)} 字节，"
        f"耗时 {elapsed_seconds:.1f} 秒）"
    )
    return tool_ctx.build_success_result(
        invocation.tool_name,
        confirmation,
        structured_content=structured_content,
    )
