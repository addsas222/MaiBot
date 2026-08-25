"""外部引擎（外置 Agent）管理接口。

统一暴露本机可部署（CLI 子进程）与网络（OpenAI 兼容 HTTP）两类外置引擎的
清单与探活测试，供 WebUI 管理控制台展示。引擎的新增/编辑经配置页完成，
这里只读运行视图并提供有界的试跑入口。
"""

import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
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
