"""Cohub OpenAI 兼容网关

将 OpenAI 兼容的 /v1/chat/completions 请求翻译为 Cohub 的
POST /spaces/{space_id}/completions 调用，供 MaiBot / dsh / opencode
等仅支持 OpenAI 协议的系统接入 Cohub 模型。

用法:
    uv run python scripts/cohub_gateway.py [--port 8787] [--space <space_id>]

认证: 读取 ~/.config/cohub/auth.json 中的 access token，过期自动刷新。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import secrets
import time
import uuid
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

logger = logging.getLogger("cohub_gateway")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

AUTH_FILE = Path.home() / ".config" / "cohub" / "auth.json"
COHUB_API_BASE = "https://api.cohub.run"
SPACE_ID = "6ec8e940-0532-4ea7-9419-8f20fc89683c"

app = FastAPI(title="Cohub OpenAI 兼容网关")
# 网关访问令牌（--api-key）；为空表示不鉴权，此时仅允许环回监听（见 __main__ 校验）
app.state.api_key = ""


def _check_gateway_auth(request: Request) -> Optional[JSONResponse]:
    """校验网关 Bearer 鉴权（第六轮审计 S2）。

    配置了 --api-key 时，两个端点均要求 ``Authorization: Bearer <key>``；
    未配置时放行（仅限环回地址监听，由启动校验保证）。
    """
    expected = app.state.api_key
    if not expected:
        return None
    # RFC 6750：scheme 大小写不敏感；恒定时间比较防时序侧信道（第七轮审计 H3）
    parts = request.headers.get("authorization", "").split(None, 1)
    if (
        len(parts) != 2
        or parts[0].lower() != "bearer"
        or not secrets.compare_digest(parts[1].strip().encode("utf-8"), expected.encode("utf-8"))
    ):
        return JSONResponse(status_code=401, content={"error": {"message": "无效的 API Key"}})
    return None


class _HideDocsWhenAuthed:
    """鉴权开启时以 404 屏蔽自动文档端点，收敛未认证可探测的路由面（第七轮审计 H3）。

    纯 ASGI 中间件实现，避免 BaseHTTPMiddleware 对 SSE 流式响应的潜在影响；
    文档路由在 FastAPI 构造期注册，事后置空 openapi_url 会导致 /docs 触发 500，
    故必须在 ASGI 层拦截。
    """

    def __init__(self, asgi_app):
        self.asgi_app = asgi_app

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] == "http"
            and app.state.api_key
            and scope["path"] in {"/docs", "/redoc", "/openapi.json"}
        ):
            not_found = JSONResponse(status_code=404, content={"error": {"message": "未找到请求的资源"}})
            await not_found(scope, receive, send)
            return
        await self.asgi_app(scope, receive, send)


app.add_middleware(_HideDocsWhenAuthed)


class AuthError(Exception):
    """认证相关错误。"""


def load_auth() -> Dict[str, Any]:
    """读取本地 Cohub 认证信息。

    Returns:
        Dict[str, Any]: auth.json 解析结果。

    Raises:
        AuthError: 认证文件缺失或格式错误。
    """
    if not AUTH_FILE.exists():
        raise AuthError(f"未找到认证文件 {AUTH_FILE}，请先运行 cohub auth login")
    try:
        return json.loads(AUTH_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AuthError(f"认证文件格式错误: {exc}") from exc


def get_access_token(auth: Dict[str, Any]) -> str:
    """获取有效 access token，过期时用 refresh token 刷新。

    Args:
        auth: auth.json 解析结果。

    Returns:
        str: 有效的 access token。

    Raises:
        AuthError: token 缺失或刷新失败。
    """
    access_token = auth.get("accessToken")
    if not access_token:
        raise AuthError("认证文件缺少 accessToken，请重新登录")
    expires_at = auth.get("accessTokenExpiresAt") or 0
    if time.time() * 1000 < expires_at - 60_000:
        return access_token
    return refresh_access_token(auth)


def refresh_access_token(auth: Dict[str, Any]) -> str:
    """使用 refresh token 刷新 access token。

    Args:
        auth: auth.json 解析结果。

    Returns:
        str: 刷新后的 access token。

    Raises:
        AuthError: 刷新失败。
    """
    refresh_token = auth.get("refreshToken")
    if not refresh_token:
        raise AuthError("access token 已过期且缺少 refreshToken，请重新运行 cohub auth login")
    issuer = auth.get("issuer") or "https://auth.neta.art"
    token_endpoint = f"{issuer}/oidc/token"
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": auth.get("clientId"),
        "resource": auth.get("resource") or "https://api.talesofai",
    }
    try:
        response = httpx.post(token_endpoint, data=payload, timeout=30)
    except httpx.HTTPError as exc:
        # 网络异常细节可能含上游端点信息，仅入日志，不对外透传（第七轮审计 S4 残留清理）
        logger.error(f"token 刷新请求失败: {exc}")
        raise AuthError("token 刷新请求失败，详情见日志") from exc
    if response.status_code != 200:
        # 上游错误体可能含内部端点/配额信息，详情仅入日志，不对外透传（第六轮审计 S4）
        logger.error(f"token 刷新失败 (HTTP {response.status_code}): {response.text[:500]}")
        raise AuthError(f"token 刷新失败 (HTTP {response.status_code})，详情见日志")
    data = response.json()
    new_access = data.get("access_token")
    if not new_access:
        # 上游响应体仅入日志，不对外透传（第七轮审计 S4 残留清理）
        logger.error(f"token 刷新响应缺少 access_token: {data}")
        raise AuthError("token 刷新响应异常，详情见日志")
    auth["accessToken"] = new_access
    if data.get("refresh_token"):
        auth["refreshToken"] = data["refresh_token"]
    auth["accessTokenExpiresAt"] = int(time.time() * 1000) + (data.get("expires_in", 3600) * 1000)
    auth["updatedAt"] = int(time.time() * 1000)
    # 原子落盘（第六轮审计 S3）：0600 权限 + 临时文件 rename，
    # 防止明文令牌随 umask 泄漏或写入中断导致文件截断
    AUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = AUTH_FILE.with_name(AUTH_FILE.name + ".tmp")
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as auth_file:
        json.dump(auth, auth_file, ensure_ascii=False, indent=2)
    os.replace(tmp_path, AUTH_FILE)
    logger.info("已刷新 Cohub access token")
    return new_access


def _content_to_blocks(content: Any) -> List[Dict[str, Any]]:
    """将 OpenAI 消息内容转换为 Cohub ContentBlock 数组。

    Args:
        content: OpenAI 消息内容（字符串或内容块数组）。

    Returns:
        List[Dict[str, Any]]: Cohub ContentBlock 数组。
    """
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    blocks: List[Dict[str, Any]] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        part_type = part.get("type")
        if part_type == "text":
            text = part.get("text", "")
            if text:
                blocks.append({"type": "text", "text": text})
        elif part_type == "image_url":
            url = part.get("image_url", {}).get("url", "")
            if url:
                blocks.append({"type": "image", "imageUrl": url})
    return blocks


def _messages_to_cohub(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """将 OpenAI 消息列表转换为 Cohub 消息列表。

    Args:
        messages: OpenAI 格式消息列表。

    Returns:
        List[Dict[str, Any]]: Cohub 格式消息列表。
    """
    result: List[Dict[str, Any]] = []
    for message in messages:
        role = message.get("role", "user")
        content = message.get("content", "")
        if isinstance(content, str) and content == "" and role != "assistant":
            continue
        if isinstance(content, list) and not content:
            continue
        blocks = _content_to_blocks(content)
        if not blocks:
            continue
        # Cohub 仅接受 user/assistant/system 角色；工具结果以 user 消息透传
        cohub_role = "user" if role == "tool" else role
        result.append({"role": cohub_role, "content": blocks})
    return result


def _blocks_to_openai_content(blocks: List[Dict[str, Any]]) -> str:
    """将 Cohub 响应 ContentBlock 数组转换为纯文本内容。

    Args:
        blocks: Cohub 响应 ContentBlock 数组。

    Returns:
        str: 拼接后的文本内容。
    """
    return "".join(block.get("text", "") for block in blocks if block.get("type") == "text")


def _blocks_to_reasoning(blocks: List[Dict[str, Any]]) -> Optional[str]:
    """从 Cohub 响应 ContentBlock 数组中提取推理内容。

    Args:
        blocks: Cohub 响应 ContentBlock 数组。

    Returns:
        Optional[str]: 推理内容，无则返回 None。
    """
    parts = [
        block.get("thinking", "")
        for block in blocks
        if block.get("type") in ("thinking", "reasoning", "reasoning_content")
    ]
    reasoning = "\n".join(part for part in parts if part).strip()
    return reasoning or None


def _to_openai_usage(usage: Any) -> Dict[str, Any]:
    """将 Cohub usage 转换为 OpenAI usage 结构。

    Args:
        usage: Cohub 返回的 usage 对象。

    Returns:
        Dict[str, Any]: OpenAI 格式 usage。
    """
    if not usage:
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    return {
        "prompt_tokens": usage.get("input") or 0,
        "completion_tokens": usage.get("output") or 0,
        "total_tokens": usage.get("totalTokens") or 0,
    }


def _build_tool_prompt(tools: List[Dict[str, Any]], *, required: bool = False) -> str:
    """将 OpenAI tools 定义转换为 XML 调用格式的提示词说明。

    Args:
        tools: OpenAI 格式的工具定义列表。
        required: 是否强制调用工具（tool_choice 指定时）。

    Returns:
        str: 注入给模型的工具说明文本。
    """
    lines: List[str] = []
    if required:
        lines.extend(
            [
                "你必须调用工具来回答本次请求，不允许直接回答或拒绝调用。",
                "",
            ]
        )
    lines.extend(["你拥有以下工具可供调用：", ""])
    for tool in tools:
        function = tool.get("function") or {}
        name = function.get("name", "")
        description = function.get("description", "")
        parameters = (function.get("parameters") or {}).get("properties") or {}
        lines.append(f"- {name}: {description}")
        if parameters:
            param_desc = "；".join(
                f"{param_name}: {param_schema.get('description', param_schema.get('type', ''))}"
                for param_name, param_schema in parameters.items()
            )
            if param_desc:
                lines.append(f"  参数: {param_desc}")
    lines.extend(
        [
            "",
            "需要调用工具时，按如下格式输出（不要输出多余文字）：",
            "<tool_call><function=工具名><parameter=参数名>参数值</parameter></function></tool_call>",
            "多个参数使用多个 <parameter> 标签；多个工具调用使用多个 <tool_call> 块。",
        ]
    )
    return "\n".join(lines)


def _inject_tool_prompt(messages: List[Dict[str, Any]], tool_prompt: str) -> List[Dict[str, Any]]:
    """将工具说明注入到消息列表（追加到 system 消息，无 system 时新增）。

    Args:
        messages: Cohub 格式消息列表。
        tool_prompt: 工具说明文本。

    Returns:
        List[Dict[str, Any]]: 注入后的消息列表。
    """
    if not messages:
        return [{"role": "system", "content": [{"type": "text", "text": tool_prompt}]}]
    if messages[0].get("role") == "system":
        system_blocks = list(messages[0].get("content") or [])
        text_blocks = [block for block in system_blocks if block.get("type") == "text"]
        combined = "\n\n".join([block.get("text", "") for block in text_blocks] + [tool_prompt])
        injected = {"role": "system", "content": [{"type": "text", "text": combined}]}
        return [injected] + messages[1:]
    return [{"role": "system", "content": [{"type": "text", "text": tool_prompt}]}] + messages


def _build_cohub_request(body: Dict[str, Any], *, stream: bool) -> Dict[str, Any]:
    """构建 Cohub completions 请求体。

    Args:
        body: OpenAI 格式请求体。
        stream: 是否流式。

    Returns:
        Dict[str, Any]: Cohub 格式请求体。
    """
    messages = _messages_to_cohub(body.get("messages") or [])
    tools = body.get("tools") or []
    if tools:
        tool_choice = body.get("tool_choice")
        required = bool(tool_choice) and tool_choice != "auto"
        messages = _inject_tool_prompt(messages, _build_tool_prompt(tools, required=required))
    cohub_body: Dict[str, Any] = {
        "provider": "cohub",
        "model": body.get("model"),
        "messages": messages,
    }
    if body.get("temperature") is not None:
        cohub_body["temperature"] = body["temperature"]
    if body.get("max_tokens") is not None:
        cohub_body["maxTokens"] = body["max_tokens"]
    elif body.get("max_completion_tokens") is not None:
        cohub_body["maxTokens"] = body["max_completion_tokens"]
    cohub_body["stream"] = stream
    return cohub_body


def _build_openai_response(
    data: Dict[str, Any],
    *,
    model: str,
    completion_id: str = "",
) -> Dict[str, Any]:
    """将 Cohub 非流式响应转换为 OpenAI 格式。

    Args:
        data: Cohub completions 响应。
        model: 模型名。
        completion_id: 完成 ID，缺省时自动生成。

    Returns:
        Dict[str, Any]: OpenAI 格式响应。
    """
    message = data.get("message") or {}
    blocks = message.get("content") or []
    content = _blocks_to_openai_content(blocks)
    reasoning = _blocks_to_reasoning(blocks)
    assistant_message: Dict[str, Any] = {"role": "assistant", "content": content}
    if reasoning:
        assistant_message["reasoning_content"] = reasoning
    stop_reason = message.get("stopReason") or "stop"
    finish_reason = "length" if stop_reason == "length" else "stop"
    return {
        "id": completion_id or f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": assistant_message,
                "finish_reason": finish_reason,
            }
        ],
        "usage": _to_openai_usage(data.get("usage")),
    }


async def _translate_stream(
    response: httpx.Response,
    *,
    model: str,
    completion_id: str,
) -> AsyncIterator[str]:
    """将 Cohub 流式 SSE 事件翻译为 OpenAI 流式格式。

    Args:
        response: Cohub 流式响应。
        model: 模型名。
        completion_id: 完成 ID。

    Yields:
        str: OpenAI 格式的 SSE 数据行。
    """
    async for line in response.aiter_lines():
        if not line.startswith("data: "):
            continue
        try:
            event = json.loads(line[len("data: ") :])
        except json.JSONDecodeError:
            continue
        event_type = event.get("type")
        if event_type == "delta":
            chunk = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": event.get("text", "")},
                        "finish_reason": None,
                    }
                ],
            }
            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
        elif event_type == "thinking_delta":
            chunk = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"reasoning_content": event.get("text", "")},
                        "finish_reason": None,
                    }
                ],
            }
            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
        elif event_type == "done":
            message = event.get("message") or {}
            blocks = message.get("content") or []
            reasoning = _blocks_to_reasoning(blocks)
            if reasoning:
                chunk = {
                    "id": completion_id,
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": model,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"reasoning_content": reasoning},
                            "finish_reason": None,
                        }
                    ],
                }
                yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
            stop_reason = message.get("stopReason") or "stop"
            finish_reason = "length" if stop_reason == "length" else "stop"
            final_chunk = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}],
                "usage": _to_openai_usage(event.get("usage")),
            }
            yield f"data: {json.dumps(final_chunk, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
        elif event_type == "error":
            # 上游错误详情仅入日志，流内只透出通用消息与错误码（第七轮审计 S4 残留清理）
            logger.error(f"Cohub 流式错误: {event.get('code')} {event.get('message')}")
            yield f"data: {json.dumps({'error': {'message': 'Cohub 流式错误，详情见服务端日志'}, 'code': event.get('code')}, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"


@app.get("/v1/models")
async def list_models(request: Request) -> Any:
    """列出网关可用的模型（转发 Cohub 模型目录）。"""
    auth_failure = _check_gateway_auth(request)
    if auth_failure is not None:
        return auth_failure
    try:
        auth = load_auth()
        token = get_access_token(auth)
    except AuthError as exc:
        return JSONResponse(status_code=401, content={"error": {"message": str(exc)}})
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                f"{COHUB_API_BASE}/api/models",
                headers={"Authorization": f"Bearer {token}"},
            )
    except httpx.HTTPError as exc:
        # 异常细节可能含上游 URL，仅入日志（第七轮审计 S4 残留清理）
        logger.error(f"获取模型列表失败: {exc}")
        return JSONResponse(status_code=502, content={"error": {"message": "获取模型列表失败，详情见服务端日志"}})
    if response.status_code != 200:
        # 上游错误体可能含内部信息，详情仅入日志（第六轮审计 S4）
        logger.warning(f"Cohub 模型目录返回异常 (HTTP {response.status_code}): {response.text[:500]}")
        return JSONResponse(
            status_code=response.status_code,
            content={"error": {"message": f"Cohub 返回 {response.status_code}，详情见服务端日志"}},
        )
    catalog = response.json()
    cohub_models = catalog.get("cohub") or []
    return {
        "object": "list",
        "data": [
            {"id": model.get("id"), "object": "model", "owned_by": "cohub"} for model in cohub_models if model.get("id")
        ],
    }


@app.post("/v1/chat/completions")
async def chat_completions(request: Request) -> Any:
    """OpenAI 兼容的聊天补全端点。"""
    auth_failure = _check_gateway_auth(request)
    if auth_failure is not None:
        return auth_failure
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return JSONResponse(status_code=400, content={"error": {"message": "请求体必须是合法 JSON"}})

    model = body.get("model")
    if not model:
        return JSONResponse(status_code=400, content={"error": {"message": "缺少 model 字段"}})
    if not body.get("messages"):
        return JSONResponse(status_code=400, content={"error": {"message": "缺少 messages 字段"}})

    try:
        auth = load_auth()
        token = get_access_token(auth)
    except AuthError as exc:
        return JSONResponse(status_code=401, content={"error": {"message": str(exc)}})

    stream = bool(body.get("stream"))
    cohub_body = _build_cohub_request(body, stream=stream)
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream" if stream else "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=30, read=600, write=60, pool=30)) as client:
            upstream = await client.post(
                f"{COHUB_API_BASE}/api/spaces/{getattr(app.state, 'space_id', SPACE_ID)}/completions",
                json=cohub_body,
                headers=headers,
            )
    except httpx.HTTPError as exc:
        # 异常细节可能含上游 URL，仅入日志（第七轮审计 S4 残留清理）
        logger.error(f"Cohub 请求失败: {exc}")
        return JSONResponse(status_code=502, content={"error": {"message": "Cohub 请求失败，详情见服务端日志"}})

    if upstream.status_code != 200:
        logger.warning(f"Cohub 上游返回异常 (HTTP {upstream.status_code}): {upstream.text[:500]}")
        return JSONResponse(
            status_code=upstream.status_code,
            content={"error": {"message": f"Cohub 返回 {upstream.status_code}，详情见服务端日志"}},
        )

    if stream:
        return StreamingResponse(
            _translate_stream(upstream, model=model, completion_id=completion_id),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    try:
        data = upstream.json()
    except json.JSONDecodeError:
        return JSONResponse(status_code=502, content={"error": {"message": "Cohub 返回非 JSON 响应"}})
    return _build_openai_response(data, model=model, completion_id=completion_id)


def main() -> None:
    parser = argparse.ArgumentParser(description="Cohub OpenAI 兼容网关")
    parser.add_argument("--port", type=int, default=8787, help="监听端口（默认 8787）")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认 127.0.0.1）")
    parser.add_argument("--space", default=SPACE_ID, help="Cohub 空间 ID")
    parser.add_argument(
        "--api-key",
        default="",
        help="网关访问令牌；绑定非环回地址时必须设置，客户端需携带 Authorization: Bearer <key>",
    )
    args = parser.parse_args()
    app.state.space_id = args.space
    app.state.api_key = args.api_key.strip()
    # 安全防呆（第六轮审计 S2）：非环回监听且未配置令牌会向局域网暴露用户 Cohub 配额，拒绝启动
    if args.host not in {"127.0.0.1", "localhost", "::1"} and not app.state.api_key:
        raise SystemExit("绑定非环回地址时必须通过 --api-key 设置访问令牌")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
