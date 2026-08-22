"""技能工具 Provider。

将技能目录中解析出的每个技能暴露为一个统一工具：模型看到的是简明描述
（skill 名 + 用途），调用后工具结果回填完整的 SKILL.md 内容，
实现按需加载技能指令（与 opencode 的技能注入机制对齐）。
"""

from __future__ import annotations

from typing import Optional

from src.core.tooling import (
    ToolAvailabilityContext,
    ToolExecutionContext,
    ToolExecutionResult,
    ToolInvocation,
    ToolProvider,
    ToolSpec,
)
from src.skills.manager import Skill, SkillManager

MAX_SKILL_CONTENT_LENGTH = 16000  # 单次回填模型上下文的技能内容上限
_TOOL_NAME_PREFIX = "skill_"


class SkillToolProvider(ToolProvider):
    """技能工具提供者。

    Attributes:
        provider_name: Provider 标识名。
        provider_type: Provider 类型。
    """

    provider_name = "skills"
    provider_type = "builtin"

    def __init__(self, manager: SkillManager) -> None:
        """初始化技能工具 Provider。

        Args:
            manager: 技能管理器实例。
        """

        self._manager = manager

    async def list_tools(
        self,
        context: Optional[ToolAvailabilityContext] = None,
    ) -> list[ToolSpec]:
        """列出全部启用的技能工具。

        禁用名单在每次列出时从全局配置读取，配置热重载后无需重启即可
        生效。

        Args:
            context: 工具可用性上下文。

        Returns:
            list[ToolSpec]: 每个启用技能对应一个工具声明。
        """

        del context
        disabled_skills = self._get_disabled_skills()
        return [
            self._build_tool_spec(skill)
            for skill in self._manager.list_skills()
            if skill.name not in disabled_skills
        ]

    @staticmethod
    def _get_disabled_skills() -> set[str]:
        """读取全局配置中的技能禁用名单。

        Returns:
            set[str]: 被禁用的技能名集合。
        """

        from src.config.config import global_config

        disabled_skills = global_config.skills.disabled_skills
        return {str(name or "").strip() for name in disabled_skills if str(name or "").strip()}

    @staticmethod
    def _get_max_content_length() -> int:
        """读取全局配置中的技能内容载入上限。

        上限取自 ``skills.max_content_length``，并夹取在安全区间内，
        避免异常配置导致上下文爆炸或技能完全不可用。

        Returns:
            int: 单次回填模型上下文的技能内容字符上限。
        """

        from src.config.config import global_config

        length = int(global_config.skills.max_content_length or MAX_SKILL_CONTENT_LENGTH)
        return max(4000, min(length, 200000))

    async def invoke(
        self,
        invocation: ToolInvocation,
        context: Optional[ToolExecutionContext] = None,
    ) -> ToolExecutionResult:
        """加载指定技能内容并回填模型上下文。

        Args:
            invocation: 工具调用请求。
            context: 执行上下文。

        Returns:
            ToolExecutionResult: 携带完整 SKILL.md 文本的工具执行结果。
        """

        del context
        skill_name = self._resolve_skill_name(invocation.tool_name)
        if skill_name is None:
            return ToolExecutionResult(
                tool_name=invocation.tool_name,
                success=False,
                error_message=f"技能工具名格式不合法: {invocation.tool_name}",
            )
        skill = self._manager.get_skill(skill_name)
        if skill is None:
            return ToolExecutionResult(
                tool_name=invocation.tool_name,
                success=False,
                error_message=f"未找到技能: {skill_name}",
            )
        content = self._manager.load_skill_content(skill_name)
        max_content_length = self._get_max_content_length()
        truncated_content = content[:max_content_length]
        if len(content) > max_content_length:
            truncated_content += "\n（技能内容过长，已截断）"
        return ToolExecutionResult(
            tool_name=invocation.tool_name,
            success=True,
            content=truncated_content,
        )

    async def close(self) -> None:
        """关闭 Provider；技能 Provider 无需释放外部资源。"""

    @classmethod
    def _build_tool_spec(cls, skill: Skill) -> ToolSpec:
        """构造单个技能的工具声明。

        Args:
            skill: 技能元信息。

        Returns:
            ToolSpec: 技能对应的统一工具声明。
        """

        tool_description = f"调用后加载技能 “{skill.name}” 的完整操作指令，并按其中说明执行。"
        if skill.description:
            tool_description = f"{skill.description} {tool_description}"
        return ToolSpec(
            name=cls._tool_name(skill.name),
            description=tool_description,
            provider_name=cls.provider_name,
            provider_type=cls.provider_type,
            parameters_schema={"type": "object", "properties": {}, "additionalProperties": False},
            metadata={"skill_name": skill.name},
        )

    @classmethod
    def _tool_name(cls, skill_name: str) -> str:
        """构造技能对应的工具名。

        Args:
            skill_name: 技能名。

        Returns:
            str: ``skill_<技能名>`` 形式的工具名。
        """

        return f"{_TOOL_NAME_PREFIX}{skill_name}"

    @classmethod
    def _resolve_skill_name(cls, tool_name: str) -> Optional[str]:
        """从工具名解析技能名。

        Args:
            tool_name: 完整工具名。

        Returns:
            Optional[str]: 技能名；格式不合法时返回 ``None``。
        """

        if not tool_name.startswith(_TOOL_NAME_PREFIX):
            return None
        skill_name = tool_name[len(_TOOL_NAME_PREFIX):]
        if not skill_name:
            return None
        return skill_name