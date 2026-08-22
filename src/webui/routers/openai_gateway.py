"""OpenAI 兼容网关（/v1/chat/completions、/v1/models）。

供 SillyTavern、Agnai 等外部 AI 前端把 LLM 源指向麦麦：以访问令牌作
API Key，请求被转交麦麦已配置的模型编排层执行。挂载在应用根路径而非
``/api/webui`` 之下，使外部应用的 base_url 可写为 ``http://host:port/v1``。
"""

import json
import time
import uuid
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from src.common.logger import get_logger
from src.config.config import global_config
from src.llm_models.payload_content.context_item import ContextItemBuilder, RoleType, get_response_text
from src.llm_models.utils_model import LLMOrchestrator
from src.webui.core.auth import is_token_valid

logger = get_logger("webui.openai_gateway")

router = APIRouter(prefix="/v1", tags=["OpenAI 兼容网关"])

_GATEWAY_TASK_NAME = "openai_gateway"
# ponytail: 非真实流式——拿到完整回复后按单块 SSE 回放；ST/Agnai 均可正常渲染
_DEFAULT_TEMPERATURE = 0.8


def _check_bearer(request: Request) -> None:
    auth = request.headers.get("authorization", "")
    token = auth.removeprefix("Bearer ").strip()
    if not token or not is_token_valid(token):
        raise HTTPException(status_code=401, detail="无效的 API Key（请使用麦麦 WebUI 访问令牌）")


@router.get("/models")
async def list_models(request: Request) -> Dict[str, Any]:
    _check_bearer(request)
    models = [
        {"id": m.name, "object": "model", "owned_by": "maibot"}
        for m in getattr(global_config, "models", [])
        if getattr(m, "name", "")
    ]
    return {"object": "list", "data": models}


def _build_context(messages: List[Dict[str, Any]]) -> List[Any]:
    role_map = {
        "system": RoleType.System,
        "user": RoleType.User,
        "assistant": RoleType.Assistant,
    }
    items = []
    for message in messages:
        role = role_map.get(str(message.get("role") or "").lower())
        if role is None:
            continue
        content = message.get("content")
        if isinstance(content, list):
            # OpenAI 多段内容：仅取文本段，图片段暂不透传
            content = "\n".join(
                str(part.get("text") or "") for part in content if isinstance(part, dict) and part.get("type") == "text"
            )
        if not str(content or "").strip():
            continue
        builder = ContextItemBuilder().set_role(role)
        builder.add_text_part(str(content))
        items.append(builder.build())
    return items


@router.post("/chat/completions")
async def chat_completions(request: Request) -> Any:
    _check_bearer(request)
    body = await request.json()
    messages = body.get("messages") or []
    if not messages:
        raise HTTPException(status_code=400, detail="messages 不能为空")

    context = _build_context(messages)
    if not context:
        raise HTTPException(status_code=400, detail="messages 中没有可用的文本内容")

    requested_model = str(body.get("model") or "").strip() or None
    orchestrator = LLMOrchestrator(_GATEWAY_TASK_NAME)
    try:
        result = await orchestrator.generate_response_with_context_async(
            lambda *args, **kwargs: context,
            temperature=body.get("temperature") or _DEFAULT_TEMPERATURE,
            max_tokens=body.get("max_tokens"),
            model_name=requested_model,
        )
    except Exception as exc:
        logger.error(f"网关生成失败: {exc}")
        raise HTTPException(status_code=502, detail=f"模型调用失败: {exc}") from None

    text = get_response_text(result.output_items).strip()
    model_name = result.model_name or requested_model or "maibot"
    created = int(time.time())
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"

    def _usage() -> Dict[str, int]:
        return {
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
            "total_tokens": result.total_tokens,
        }

    def _delta_chunk(delta: Dict[str, Any], finish_reason: Any = None) -> str:
        payload = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model_name,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
        }
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    if bool(body.get("stream")):
        async def stream_response():
            yield _delta_chunk({"role": "assistant"})
            if text:
                yield _delta_chunk({"content": text})
            yield _delta_chunk({}, finish_reason="stop")
            usage_payload = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "model": model_name,
                "usage": _usage(),
            }
            yield f"data: {json.dumps(usage_payload, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(stream_response(), media_type="text/event-stream")

    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": created,
        "model": model_name,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": _usage(),
    }
