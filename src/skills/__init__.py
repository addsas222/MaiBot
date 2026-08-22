"""技能（Skills）模块。

技能采用 opencode 的 SKILL.md 约定：技能目录内含带 YAML frontmatter 的
``SKILL.md`` 文件，管理器负责扫描与解析，工具 Provider 将每个技能暴露为
可按需加载的统一工具（``skill_<技能名>``），调用结果把完整技能内容
回填进模型上下文。
"""

from src.skills.manager import Skill, SkillManager, get_skill_manager
from src.skills.provider import SkillToolProvider

__all__ = ["Skill", "SkillManager", "SkillToolProvider", "get_skill_manager"]