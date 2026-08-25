"""动态管理员列表管理接口。

供 WebUI 查看与维护运行时管理员动态列表（``admin_users`` 表），
与聊天指令 ``/admin list|add|remove`` 共用同一套服务逻辑。
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from src.services.admin_user_service import add_admin, list_admins, remove_admin
from src.webui.dependencies import require_auth

router = APIRouter(prefix="/admin-users", tags=["admin-users"], dependencies=[Depends(require_auth)])


class AddAdminRequest(BaseModel):
    """新增管理员请求体。"""

    user_id: str = Field(min_length=1, description="管理员用户 ID")
    platform: str = Field(default="qq", description="平台名；空串表示通配全平台")
    note: str = Field(default="", description="备注")


@router.get("")
async def get_admin_users() -> dict[str, Any]:
    """列出全部管理员条目。"""
    admins = await list_admins()
    return {"success": True, "items": admins, "total": len(admins)}


@router.post("")
async def create_admin_user(payload: AddAdminRequest) -> dict[str, Any]:
    """新增管理员条目。"""
    try:
        created = await add_admin(payload.user_id, platform=payload.platform, note=payload.note)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"success": True, "item": created}


@router.delete("/{user_id}")
async def delete_admin_user(
    user_id: str,
    platform: str = Query(default="qq", description="平台名；需与条目匹配"),
) -> dict[str, Any]:
    """移除管理员条目（受"至少保留一人"防锁死保护约束）。"""
    try:
        await remove_admin(user_id, platform=platform)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"success": True}
