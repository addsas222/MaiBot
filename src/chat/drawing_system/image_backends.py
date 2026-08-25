"""绘图后端适配层：OpenAI 兼容 / ComfyUI / SD-WebUI 三种生成后端。

统一约定：后端函数返回 PNG 图片二进制；HTTP 错误、超时、响应形状异常
一律直接抛出，不做兜底掩盖。
"""

from base64 import b64decode
from binascii import Error as BinasciiError
from pathlib import Path
from time import monotonic
from typing import Any
from urllib.parse import urlparse

import asyncio
import ipaddress
import json
import socket

import httpx

from src.common.http_client import get_main_http_client
from src.common.logger import get_logger
from src.config.config import global_config
from src.config.official_configs import (
    ComfyUIImageBackend,
    OpenAICompatImageBackend,
    SDWebUIImageBackend,
)

logger = get_logger("drawing_backends")

_COMFYUI_CLIENT_ID = "maibot-drawing"
_POLL_INTERVAL_START_SECONDS = 0.5
_POLL_INTERVAL_MAX_SECONDS = 2.0


def _strip_data_uri_prefix(b64_str: str) -> str:
    """剥离 data:image/...;base64 前缀，返回纯 base64 文本。"""
    text = str(b64_str or "").strip()
    if text.lower().startswith("data:"):
        _, separator, payload = text.partition(",")
        if not separator or not payload.strip():
            raise ValueError(f"data URI 缺少有效载荷: {text[:64]}")
        return payload.strip()
    return text


def _decode_strict_base64(b64_str: str) -> bytes:
    """严格校验并解码 base64 图片数据，失败时抛出异常。"""
    cleaned = _strip_data_uri_prefix(b64_str)
    try:
        data = b64decode(cleaned, validate=True)
    except (BinasciiError, ValueError) as exc:
        raise ValueError(f"图片 base64 数据校验失败: {exc}") from exc
    if not data:
        raise ValueError("图片 base64 数据解码后为空")
    return data


def _resolve_unsafe_image_host(hostname: str) -> bool:
    """判断主机名是否解析到禁止访问的地址（环回/私网/链路本地/组播/保留段）。

    解析失败按不安全处理前先抛出明确错误，不做兜底掩盖。
    """

    try:
        addr_infos = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError(f"无法解析图片 URL 主机名: {hostname}") from exc
    for info in addr_infos:
        address = ipaddress.ip_address(info[4][0])
        if (
            address.is_loopback
            or address.is_private
            or address.is_link_local
            or address.is_multicast
            or address.is_reserved
            or address.is_unspecified
        ):
            return True
    return False


def _validate_download_url(url: str, backend_base_url: str) -> None:
    """校验服务端下发的图片 URL，阻断借响应 URL 发起的 SSRF 中转。

    仅允许 http(s)；私网/环回目标只在与绘图后端 base_url 同源时放行
    （自托管 ComfyUI/SD-WebUI 回传自身内网地址属正常形态）。
    """

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"图片 URL 协议不被允许，仅支持 http/https: {url}")
    hostname = parsed.hostname
    if not hostname:
        raise ValueError(f"图片 URL 缺少主机名: {url}")
    if hostname == urlparse(backend_base_url).hostname:
        return
    if _resolve_unsafe_image_host(hostname):
        raise ValueError(f"图片 URL 指向私网/保留地址，已拒绝下载: {hostname}")


async def _download_image(url: str, timeout: httpx.Timeout, backend_base_url: str) -> bytes:
    """下载远端图片并返回二进制内容。"""
    _validate_download_url(url, backend_base_url)
    client = get_main_http_client()
    response = await client.get(url, timeout=timeout, follow_redirects=True)
    response.raise_for_status()
    if not response.content:
        raise ValueError(f"图片下载结果为空: {url}")
    return response.content


def _first_dict_item(items: Any) -> dict[str, Any] | None:
    """取列表首个字典元素，形状不符时返回 None。"""
    if isinstance(items, list) and items and isinstance(items[0], dict):
        return items[0]
    return None


async def openai_compat_generate(
    prompt: str,
    *,
    cfg: OpenAICompatImageBackend | None = None,
    size: str = "",
    timeout: float | None = None,
) -> bytes:
    """通过 OpenAI 兼容 /images/generations 接口生成图片。

    兼容三种响应形状：data[].b64_json、data[].url、images[].url（SiliconFlow 变体，
    url 需再 GET 下载）。
    """
    backend_cfg = cfg if cfg is not None else global_config.image_generation.openai_compat
    effective_timeout = timeout if timeout is not None else global_config.image_generation.timeout_seconds
    base_url = str(backend_cfg.base_url or "").strip().rstrip("/")
    model = str(backend_cfg.model or "").strip()
    if not base_url:
        raise ValueError("openai_compat 绘图后端未配置 base_url")
    if not model:
        raise ValueError("openai_compat 绘图后端未配置 model")

    effective_size = str(size or backend_cfg.size or "").strip()
    payload: dict[str, Any] = {"model": model, "prompt": prompt, "response_format": "b64_json"}
    if effective_size:
        payload["size"] = effective_size
    headers = {"Authorization": f"Bearer {backend_cfg.api_key}"} if backend_cfg.api_key else {}

    client = get_main_http_client()
    request_timeout = httpx.Timeout(effective_timeout)
    response = await client.post(
        f"{base_url}/images/generations",
        json=payload,
        headers=headers,
        timeout=request_timeout,
    )
    response.raise_for_status()
    body = response.json()

    if isinstance(body, dict):
        first_data = _first_dict_item(body.get("data"))
        if first_data is not None:
            b64_value = first_data.get("b64_json")
            if isinstance(b64_value, str) and b64_value.strip():
                return _decode_strict_base64(b64_value)
            url_value = first_data.get("url")
            if isinstance(url_value, str) and url_value.strip():
                return await _download_image(url_value.strip(), request_timeout, base_url)
        first_image = _first_dict_item(body.get("images"))
        if first_image is not None:
            url_value = first_image.get("url")
            if isinstance(url_value, str) and url_value.strip():
                return await _download_image(url_value.strip(), request_timeout, base_url)

    body_keys = ", ".join(body.keys()) if isinstance(body, dict) else type(body).__name__
    raise ValueError(f"绘图响应中未找到可用的图片数据，响应顶层键: {body_keys}")


def _inject_workflow_prompt(workflow: dict[str, Any], node_id: str, text: str) -> None:
    """向工作流的指定节点注入提示词文本。"""
    node = workflow.get(node_id)
    if not isinstance(node, dict) or not isinstance(node.get("inputs"), dict):
        raise ValueError(f"ComfyUI 工作流中找不到可注入的提示词节点: {node_id}")
    node["inputs"]["text"] = text


async def comfyui_generate(
    prompt: str,
    *,
    cfg: ComfyUIImageBackend | None = None,
    negative_prompt: str = "",
    timeout: float | None = None,
) -> bytes:
    """通过 ComfyUI 的 /prompt 接口排队工作流并轮询 /history 下载成图。"""
    backend_cfg = cfg if cfg is not None else global_config.image_generation.comfyui
    effective_timeout = timeout if timeout is not None else global_config.image_generation.timeout_seconds
    base_url = str(backend_cfg.base_url or "").strip().rstrip("/")
    workflow_path_text = str(backend_cfg.workflow_path or "").strip()
    if not base_url:
        raise ValueError("ComfyUI 绘图后端未配置 base_url")
    if not workflow_path_text:
        raise ValueError("ComfyUI 绘图后端未配置 workflow_path")

    workflow_path = Path(workflow_path_text)
    try:
        workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"ComfyUI 工作流文件不存在: {workflow_path}") from exc
    if not isinstance(workflow, dict):
        raise ValueError(f"ComfyUI 工作流文件顶层不是 JSON 对象: {workflow_path}")

    prompt_node_id = str(backend_cfg.prompt_node_id or "").strip()
    if not prompt_node_id:
        raise ValueError("ComfyUI 绘图后端未配置 prompt_node_id")
    _inject_workflow_prompt(workflow, prompt_node_id, prompt)

    negative_node_id = str(backend_cfg.negative_prompt_node_id or "").strip()
    effective_negative = str(negative_prompt or "").strip()
    if negative_node_id and effective_negative:
        _inject_workflow_prompt(workflow, negative_node_id, effective_negative)

    client = get_main_http_client()
    request_timeout = httpx.Timeout(effective_timeout)
    submit_response = await client.post(
        f"{base_url}/prompt",
        json={"prompt": workflow, "client_id": _COMFYUI_CLIENT_ID},
        timeout=request_timeout,
    )
    submit_response.raise_for_status()
    submit_body = submit_response.json()
    prompt_id = str(submit_body.get("prompt_id") or "").strip() if isinstance(submit_body, dict) else ""
    if not prompt_id:
        raise ValueError(f"ComfyUI 提交响应缺少 prompt_id: {submit_body}")

    deadline = monotonic() + effective_timeout
    poll_interval = _POLL_INTERVAL_START_SECONDS
    history_entry: dict[str, Any] | None = None
    while True:
        remaining = deadline - monotonic()
        if remaining <= 0:
            break
        # 轮询间隔 0.5s 起步逐次加倍至 2s 封顶，避免打爆服务；
        # 单次轮询请求超时收紧到剩余时间，保证总耗时不超过配置上限。
        await asyncio.sleep(min(poll_interval, remaining))
        poll_interval = min(poll_interval * 2, _POLL_INTERVAL_MAX_SECONDS)
        history_response = await client.get(
            f"{base_url}/history/{prompt_id}",
            timeout=httpx.Timeout(remaining),
        )
        history_response.raise_for_status()
        history_body = history_response.json()
        if not isinstance(history_body, dict):
            continue
        entry = history_body.get(prompt_id)
        if not isinstance(entry, dict):
            continue
        # 执行失败（如显存不足、节点校验失败）时 ComfyUI 会写入 status_str="error"
        # 且 outputs 为空，必须立即抛出真实原因，而不是空转到超时被误报。
        status_field = entry.get("status")
        if isinstance(status_field, dict) and str(status_field.get("status_str") or "") == "error":
            raise RuntimeError(f"ComfyUI 工作流执行失败: prompt_id={prompt_id}, status={status_field}")
        outputs = entry.get("outputs")
        if isinstance(outputs, dict) and outputs:
            history_entry = entry
            break
    if history_entry is None:
        raise TimeoutError(f"等待 ComfyUI 出图超时: prompt_id={prompt_id}, 上限 {effective_timeout} 秒")

    outputs = history_entry["outputs"]
    for node_output in outputs.values():
        if not isinstance(node_output, dict):
            continue
        image_info = _first_dict_item(node_output.get("images"))
        if image_info is None:
            continue
        filename = str(image_info.get("filename") or "").strip()
        if not filename:
            continue
        view_response = await client.get(
            f"{base_url}/view",
            params={
                "filename": filename,
                "subfolder": str(image_info.get("subfolder") or ""),
                "type": str(image_info.get("type") or ""),
            },
            timeout=request_timeout,
        )
        view_response.raise_for_status()
        if not view_response.content:
            raise ValueError(f"ComfyUI 图片下载结果为空: filename={filename}")
        return view_response.content
    raise ValueError(f"ComfyUI 历史结果中没有可用图片输出: prompt_id={prompt_id}")


async def sd_webui_generate(
    prompt: str,
    *,
    cfg: SDWebUIImageBackend | None = None,
    negative_prompt: str | None = None,
    timeout: float | None = None,
) -> bytes:
    """通过 SD-WebUI 的 /sdapi/v1/txt2img 接口生成图片。

    响应 images 为裸 base64 列表（可能带 data:image 前缀），取第一张解码。
    """
    backend_cfg = cfg if cfg is not None else global_config.image_generation.sd_webui
    effective_timeout = timeout if timeout is not None else global_config.image_generation.timeout_seconds
    base_url = str(backend_cfg.base_url or "").strip().rstrip("/")
    if not base_url:
        raise ValueError("SD-WebUI 绘图后端未配置 base_url")

    if negative_prompt is None:
        effective_negative = str(backend_cfg.negative_prompt or "")
    else:
        effective_negative = str(negative_prompt)

    payload = {
        "prompt": prompt,
        "negative_prompt": effective_negative,
        "width": int(backend_cfg.width),
        "height": int(backend_cfg.height),
        "steps": int(backend_cfg.steps),
        "cfg_scale": float(backend_cfg.cfg_scale),
        "sampler_name": str(backend_cfg.sampler_name),
    }
    client = get_main_http_client()
    response = await client.post(
        f"{base_url}/sdapi/v1/txt2img",
        json=payload,
        timeout=httpx.Timeout(effective_timeout),
    )
    response.raise_for_status()
    body = response.json()
    first_image = body.get("images")[0] if isinstance(body, dict) and isinstance(body.get("images"), list) and body["images"] else None
    if not isinstance(first_image, str) or not first_image.strip():
        raise ValueError("SD-WebUI 响应中未找到图片数据")
    return _decode_strict_base64(first_image)
