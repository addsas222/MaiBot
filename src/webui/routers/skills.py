"""技能（Skills）管理接口。

提供技能列表与详情读取接口，供 WebUI 的技能管理页面展示。
技能内容的读取通过 SkillManager 完成，与运行时工具 Provider 共用同一套
扫描与解析逻辑。
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.config.config import global_config
from src.skills.manager import get_skill_manager
from src.webui.dependencies import require_auth

router = APIRouter(prefix="/skills", tags=["skills"], dependencies=[Depends(require_auth)])


class SkillPreview(BaseModel):
    """技能列表项。"""

    name: str = Field(description="技能名称")
    description: str = Field(default="", description="技能用途描述")
    version: str = Field(default="", description="技能版本号")
    directory: str = Field(default="", description="技能所在目录")
    enabled: bool = Field(default=True, description="技能是否启用（未被禁用名单拦截）")


class SkillDetail(SkillPreview):
    """技能详情。"""

    content: str = Field(default="", description="SKILL.md 完整内容")


@router.get("")
async def list_skills() -> dict[str, Any]:
    """列出全部可用技能。

    Returns:
        dict[str, Any]: 技能列表响应。
    """

    manager = get_skill_manager()
    disabled_skills = global_config.skills.disabled_skills
    skills = [
        SkillPreview(
            name=skill.name,
            description=skill.description,
            version=skill.version,
            directory=str(skill.directory),
            enabled=skill.name not in disabled_skills,
        )
        for skill in manager.list_skills()
    ]
    return {"success": True, "skills": skills}


@router.get("/{skill_name}")
async def get_skill_detail(skill_name: str) -> dict[str, Any]:
    """读取单个技能的完整内容。

    Args:
        skill_name: 技能名称。

    Returns:
        dict[str, Any]: 技能详情响应。
    """

    manager = get_skill_manager()
    skill = manager.get_skill(skill_name)
    if skill is None:
        raise HTTPException(status_code=404, detail=f"未找到技能: {skill_name}")
    content = manager.load_skill_content(skill_name)
    detail = SkillDetail(
        name=skill.name,
        description=skill.description,
        version=skill.version,
        directory=str(skill.directory),
        content=content or "",
    )
    return {"success": True, "skill": detail}