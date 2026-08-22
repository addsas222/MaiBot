"""外部应用（SillyTavern / Agnai）管理接口。

提供安装、启动、停止、健康检查与配置覆盖能力，供 WebUI「外部应用」
页面展示与控制；应用界面通过 iframe 嵌入（见 embed_url）。
"""

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field

from src.services.external_app_service import get_external_app_service
from src.webui.dependencies import require_auth

router = APIRouter(prefix="/external-apps", tags=["外部应用"], dependencies=[Depends(require_auth)])


class ExternalAppOverride(BaseModel):
    """外部应用配置覆盖项。"""

    external_url: str = Field(default="", description="外挂模式：已运行实例的地址，配置后不再托管进程")
    port: int | None = Field(default=None, description="本地托管端口")
    install_steps: list[list[str]] | None = Field(default=None, description="安装步骤（argv 列表的列表）")
    start_cmd: list[str] | None = Field(default=None, description="启动命令 argv")
    env: dict[str, str] | None = Field(default=None, description="附加环境变量")


@router.get("")
async def list_external_apps() -> dict[str, Any]:
    service = get_external_app_service()
    return {"success": True, "apps": await service.list_apps(), "active_engine": service.get_active_engine()}


@router.post("/{app_id}/activate")
async def activate_external_app(app_id: str) -> dict[str, Any]:
    """启用为当前子内核；同一时刻仅允许一个。"""
    try:
        return await get_external_app_service().set_active_engine(app_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None


@router.post("/deactivate")
async def deactivate_sub_engine() -> dict[str, Any]:
    """停用全部子内核。"""
    return await get_external_app_service().set_active_engine(None)


@router.get("/{app_id}")
async def get_external_app(app_id: str) -> dict[str, Any]:
    try:
        status = await get_external_app_service().app_status(app_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    return {"success": True, "app": {**status, "embed_url": get_external_app_service().embed_url(app_id)}}


@router.post("/{app_id}/install")
async def install_external_app(app_id: str) -> dict[str, Any]:
    try:
        return await get_external_app_service().install(app_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None


@router.post("/{app_id}/start")
async def start_external_app(app_id: str) -> dict[str, Any]:
    try:
        return await get_external_app_service().start(app_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None


@router.post("/{app_id}/stop")
async def stop_external_app(app_id: str) -> dict[str, Any]:
    try:
        return await get_external_app_service().stop(app_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None


@router.get("/{app_id}/health")
async def health_external_app(app_id: str) -> dict[str, Any]:
    try:
        return await get_external_app_service().health(app_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None


@router.get("/{app_id}/log")
async def log_external_app(
    app_id: str,
    kind: str = "run",
    last: int = 200,
) -> dict[str, Any]:
    if kind not in {"install", "run"}:
        raise HTTPException(status_code=400, detail="kind 仅支持 install/run")
    text = await get_external_app_service().read_log(app_id, kind, last)
    return {"success": True, "log": text}


@router.put("/{app_id}/config")
async def config_external_app(app_id: str, payload: ExternalAppOverride = Body(...)) -> dict[str, Any]:
    try:
        status = await get_external_app_service().save_override(app_id, payload.model_dump(exclude_none=True))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    return {"success": True, "app": {**status, "embed_url": get_external_app_service().embed_url(app_id)}}
