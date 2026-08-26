"""外部引擎（外置 Agent）管理接口。

统一暴露本机可部署（CLI 子进程）与网络（OpenAI 兼容 HTTP）两类外置引擎的
完整 CRUD 管理与探活测试，供 WebUI 独立管理页展示与操作。

引擎配置持久化到 bot_config.toml 的 [external_agent] 段，
修改后自动触发热重载以更新运行时连接。
"""

import asyncio
import os
import time
from typing import Any, Dict

import tomlkit
from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field

from src.config.config import global_config
from src.services.external_agent_service import list_agents, run_agent
from src.webui.dependencies import require_auth

router = APIRouter(
    prefix="/external-engines",
    tags=["external-engines"],
    dependencies=[Depends(require_auth)],
)

_TEST_PREVIEW_CHARS = 400
_CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "..", "config")
_BOT_CONFIG_PATH = os.path.join(_CONFIG_DIR, "bot_config.toml")


class EngineTestRequest(BaseModel):
    """引擎探活请求体。"""

    question: str = Field(default="连通性测试：请直接回复 pong", description="发送给引擎的探活问题")


@router.get("")
async def get_external_engines() -> dict[str, Any]:
    """列出全部已配置的外置引擎及功能开关状态。"""
    agents = await list_agents()
    return {
        "success": True,
        "enable": bool(global_config.external_agent.enable),
        "items": agents,
        "total": len(agents),
    }


@router.post("/{name}/test")
async def test_external_engine(name: str, payload: EngineTestRequest) -> dict[str, Any]:
    """对指定引擎执行一次真实探活调用并返回耗时与输出预览。

    引擎自身的超时配置在此生效；失败（含未启用）以 502 原样透出原因。
    """
    if not global_config.external_agent.enable:
        raise HTTPException(status_code=409, detail="外部Agent功能未启用（external_agent.enable）")

    started = time.monotonic()
    try:
        output = await run_agent(name, payload.question)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"引擎 {name!r} 调用失败: {exc}") from exc

    elapsed_ms = int((time.monotonic() - started) * 1000)
    return {
        "success": True,
        "engine": name,
        "elapsed_ms": elapsed_ms,
        "output_chars": len(output),
        "preview": output[:_TEST_PREVIEW_CHARS],
    }


class CliEnginePayload(BaseModel):
    name: str = Field(min_length=1)
    command: list[str] = Field(min_length=1)
    working_dir: str = ""
    timeout_seconds: float = 300.0
    max_output_chars: int = 4000


class HttpEnginePayload(BaseModel):
    name: str = Field(min_length=1)
    base_url: str = Field(min_length=1)
    api_key: str = ""
    model: str = ""
    system_prompt: str = ""
    timeout_seconds: float = 120.0


def _read_bot_config_toml() -> Any:
    with open(_BOT_CONFIG_PATH, encoding="utf-8") as f:
        return tomlkit.load(f)


def _write_bot_config_toml(doc: Any) -> None:
    with open(_BOT_CONFIG_PATH, "w", encoding="utf-8") as f:
        f.write(tomlkit.dumps(doc))


def _engine_to_payload(entry: Any, kind: str) -> dict:
    d = {"name": entry.name, "kind": kind}
    if kind == "cli":
        d.update(command=list(entry.command), working_dir=entry.working_dir,
                 timeout_seconds=entry.timeout_seconds, max_output_chars=entry.max_output_chars)
    else:
        d.update(base_url=entry.base_url, api_key=entry.api_key, model=entry.model,
                 system_prompt=entry.system_prompt, timeout_seconds=entry.timeout_seconds)
    return d


@router.get("/config")
async def get_engines_config() -> Dict[str, Any]:
    """返回全部引擎配置（含 CLI 和 HTTP），供前端编辑。"""
    cfg = global_config.external_agent
    cli = [_engine_to_payload(e, "cli") for e in cfg.cli_endpoints]
    http = [_engine_to_payload(e, "http") for e in cfg.http_endpoints]
    return {"success": True, "enable": bool(cfg.enable), "cli": cli, "http": http}


@router.put("/config")
async def save_engines_config(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """保存引擎列表到 bot_config.toml 并触发热重载。"""
    doc = await asyncio.to_thread(_read_bot_config_toml)
    ext = doc.get("external_agent")
    if ext is None:
        raise HTTPException(status_code=404, detail="bot_config.toml 缺少 [external_agent] 段")

    cli_items = body.get("cli", [])
    http_items = body.get("http", [])

    def _cli_row(item: dict) -> Any:
        t = tomlkit.table()
        t["name"] = item["name"]
        t["command"] = item.get("command", [])
        t["working_dir"] = item.get("working_dir", "")
        t["timeout_seconds"] = float(item.get("timeout_seconds", 300))
        t["max_output_chars"] = int(item.get("max_output_chars", 4000))
        return t

    def _http_row(item: dict) -> Any:
        t = tomlkit.table()
        t["name"] = item["name"]
        t["transport"] = "streamable_http"
        t["url"] = item.get("base_url", "")
        t["api_key"] = item.get("api_key", "")
        t["model"] = item.get("model", "")
        t["system_prompt"] = item.get("system_prompt", "")
        t["timeout_seconds"] = float(item.get("timeout_seconds", 120))
        return t

    ext["cli_endpoints"] = [_cli_row(x) for x in cli_items]
    ext["http_endpoints"] = [_http_row(x) for x in http_items]
    await asyncio.to_thread(_write_bot_config_toml, doc)

    from src.config.config import config_manager
    await config_manager.reload_config(changed_scopes=["bot"])
    return {"success": True}
