"""外部 Agent 执行器：支持 OpenAI 兼容 HTTP 端点与本机 CLI 子进程两种形态。"""

from types import SimpleNamespace

import asyncio
import os
import signal

import httpx

from src.common.http_client import get_main_http_client
from src.common.logger import get_logger
from src.config.config import global_config
from src.config.official_configs import ExternalAgentCliEndpoint, ExternalAgentHttpEndpoint

logger = get_logger("external_agent_service")

_HTTP_BODY_ERROR_SNIPPET_CHARS = 500
_STDERR_TAIL_CHARS = 500
_CLI_OUTPUT_TRUNCATION_SUFFIX = "…(已截断)"


def _normalize_name(name: str) -> str:
    """剥除空白的 Agent 名称。"""

    return str(name or "").strip()


async def list_agents() -> list[dict]:
    """合并列出 HTTP 与 CLI 两类已配置的外部 Agent 摘要。"""

    config = global_config.external_agent
    agents: list[dict] = []
    for endpoint in config.http_endpoints:
        agents.append(
            {
                "kind": "http",
                "name": endpoint.name,
                "base_url": endpoint.base_url,
                "model": endpoint.model,
                "timeout_seconds": endpoint.timeout_seconds,
            }
        )
    for endpoint in config.cli_endpoints:
        agents.append(
            {
                "kind": "cli",
                "name": endpoint.name,
                "command": list(endpoint.command),
                "timeout_seconds": endpoint.timeout_seconds,
                "max_output_chars": endpoint.max_output_chars,
            }
        )
    return agents


def _find_endpoint(name: str) -> tuple[str, ExternalAgentHttpEndpoint | ExternalAgentCliEndpoint]:
    """按名称在两类端点中查找；找不到时抛 ValueError 并列出可用名。"""

    normalized = _normalize_name(name)
    if not normalized:
        raise ValueError("Agent 名称不能为空")
    config = global_config.external_agent
    for endpoint in config.http_endpoints:
        if _normalize_name(endpoint.name) == normalized:
            return "http", endpoint
    for endpoint in config.cli_endpoints:
        if _normalize_name(endpoint.name) == normalized:
            return "cli", endpoint
    available = [
        _normalize_name(endpoint.name)
        for endpoint in [*config.http_endpoints, *config.cli_endpoints]
        if _normalize_name(endpoint.name)
    ]
    raise ValueError(
        f"未找到名为 {name!r} 的外部 Agent，当前可用：{', '.join(available) if available else '（无）'}"
    )


async def run_agent(name: str, question: str) -> str:
    """执行指定外部 Agent 并返回其文本输出。

    Args:
        name: 配置中的 Agent 名称。
        question: 提问文本，剥除空白后必须非空。

    Raises:
        ValueError: 名称未配置或问题为空。
        RuntimeError: 功能未启用、HTTP 响应异常或 CLI 进程退出码非零。
        TimeoutError: CLI 子进程执行超时。
    """

    kind, endpoint = _find_endpoint(name)

    if not global_config.external_agent.enable:
        raise RuntimeError("外部Agent功能未启用")
    normalized_question = str(question or "").strip()
    if not normalized_question:
        raise ValueError("问题内容不能为空")

    if kind == "http":
        return await _run_http_endpoint(endpoint, normalized_question)
    return await _run_cli_endpoint(endpoint, normalized_question)


async def _run_http_endpoint(endpoint: ExternalAgentHttpEndpoint, question: str) -> str:
    """调用 OpenAI 兼容 ``/chat/completions`` 端点并解析回复文本。"""

    messages: list[dict[str, str]] = []
    system_prompt = str(endpoint.system_prompt or "").strip()
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": question})

    headers: dict[str, str] = {}
    api_key = str(endpoint.api_key or "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    client = get_main_http_client()
    response = await client.post(
        f"{endpoint.base_url.rstrip('/')}/chat/completions",
        json={"model": endpoint.model, "messages": messages},
        headers=headers,
        timeout=httpx.Timeout(endpoint.timeout_seconds),
    )
    if not (200 <= response.status_code < 300):
        raise RuntimeError(
            f"外部Agent {endpoint.name!r} HTTP请求失败："
            f"status={response.status_code} body={response.text[:_HTTP_BODY_ERROR_SNIPPET_CHARS]}"
        )

    payload = response.json()
    choices = payload.get("choices") or []
    if not choices:
        raise RuntimeError(
            f"外部Agent {endpoint.name!r} 响应缺少 choices：{str(payload)[:_HTTP_BODY_ERROR_SNIPPET_CHARS]}"
        )
    choice = choices[0]
    content = str((choice.get("message") or {}).get("content") or "").strip()
    if not content:
        finish_reason = choice.get("finish_reason")
        raise RuntimeError(
            f"外部Agent {endpoint.name!r} 返回空内容（finish_reason={finish_reason}）"
        )
    logger.info(f"外部Agent {endpoint.name!r} 调用成功，回复长度={len(content)}")
    return content


async def _run_cli_endpoint(endpoint: ExternalAgentCliEndpoint, question: str) -> str:
    """以子进程方式执行 CLI 形态 Agent，问题文本追加在命令参数末位。"""

    command = [str(part) for part in endpoint.command]
    if not command:
        raise ValueError(f"外部Agent {endpoint.name!r} 的 command 配置为空")
    working_dir = str(endpoint.working_dir or "").strip() or None

    process = await asyncio.create_subprocess_exec(
        *command,
        question,
        cwd=working_dir,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(),
            timeout=endpoint.timeout_seconds,
        )
    except asyncio.TimeoutError:
        # 超时先杀整个进程组再回收：CLI Agent 常派生孙进程，只 kill 直接子进程会留下孤儿
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except ProcessLookupError:
            # 进程已自行退出，直接回收即可
            pass
        await process.wait()
        raise TimeoutError(
            f"外部Agent {endpoint.name!r} 执行超时（{endpoint.timeout_seconds} 秒），进程已被终止"
        ) from None

    stderr_text = stderr_bytes.decode("utf-8", errors="replace").strip()
    if process.returncode != 0:
        raise RuntimeError(
            f"外部Agent {endpoint.name!r} 执行失败（returncode={process.returncode}）："
            f"{stderr_text[-_STDERR_TAIL_CHARS:]}"
        )

    output = stdout_bytes.decode("utf-8", errors="replace").strip()
    if len(output) > endpoint.max_output_chars:
        output = output[: endpoint.max_output_chars] + _CLI_OUTPUT_TRUNCATION_SUFFIX
    logger.info(
        f"外部Agent {endpoint.name!r} 子进程执行成功，returncode={process.returncode} 输出长度={len(output)}"
    )
    return output


# 单一导入入口：既可 ``from src.services.external_agent_service import run_agent``，
# 也可 ``from src.services import external_agent_service`` 后以模块属性调用。
external_agent_service = SimpleNamespace(
    list_agents=list_agents,
    run_agent=run_agent,
)
