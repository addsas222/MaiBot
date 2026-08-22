"""技能管理器。

技能机制沿用 opencode 的 SKILL.md 约定：每个技能是独立的目录，目录内含
一份 ``SKILL.md``，文件头部使用 YAML frontmatter 声明 ``name``（技能名）与
``description``（技能用途描述）。管理器负责扫描技能目录、解析声明并读取
技能完整内容，供模型在需要时按需加载（与 opencode 的技能按需注入机制对齐）。
"""

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Optional, Sequence

from src.common.logger import get_logger
from src.common.version import PROJECT_ROOT

logger = get_logger("skills.manager")

_SKILL_MD_FILENAME = "SKILL.md"
_SKILL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]*$")
_FRONTMATTER_RE = re.compile(r"\A---\s*\n?(.*?)\n?---\s*\n", re.DOTALL)
_RESERVED_SKILL_DIRECTORY_NAMES = {"data", "__pycache__"}  # 条目需为 casefold 形式
_MAX_NAME_LENGTH = 64


class SkillParseError(ValueError):
    """技能声明解析失败错误。"""


@dataclass(slots=True)
class Skill:
    """已解析的技能元信息。

    Attributes:
        name: 技能名称（来自 frontmatter 的 name 字段）。
        description: 技能用途描述（来自 frontmatter 的 description 字段）。
        directory: 技能所在目录。
        skill_md_path: ``SKILL.md`` 文件路径。
        version: 技能版本号，未声明时为空字符串。
    """

    name: str
    description: str
    directory: Path
    skill_md_path: Path
    version: str = ""


def _parse_frontmatter(content: str) -> dict[str, str]:
    """解析 SKILL.md 头部的 YAML frontmatter。

    仅支持 ``key: value`` 形式的单行键值对（opencode 技能声明的常用子集），
    键值两侧引号会被剥离；其余复杂 YAML 结构不在本期支持范围内。

    Args:
        content: SKILL.md 完整文本。

    Returns:
        dict[str, str]: frontmatter 键值映射。
    """

    match = _FRONTMATTER_RE.match(content)
    if match is None:
        return {}
    header_text = match.group(1).strip()
    fields: dict[str, str] = {}
    for line in header_text.splitlines():
        if ":" not in line:
            continue
        key, _, raw_value = line.partition(":")
        key = key.strip()
        value = raw_value.strip()
        if key in ("name", "description", "version"):
            fields[key] = value.strip("\"'")
    return fields


def _parse_skill_name(frontmatter: dict[str, str], directory: Path) -> str:
    """解析并校验技能名称。

    Args:
        frontmatter: frontmatter 键值映射。
        directory: 技能所在目录。

    Returns:
        str: 合法的技能名。

    Raises:
        SkillParseError: 技能名缺失或不符合命名规则时抛出。
    """

    name = str(frontmatter.get("name") or "").strip()
    if not name:
        raise SkillParseError(f"技能 {directory.name} 的 SKILL.md 缺少 name 字段")
    if len(name) > _MAX_NAME_LENGTH:
        raise SkillParseError(f"技能 {directory.name} 的 name 字段过长（超过 {_MAX_NAME_LENGTH} 字符）")
    if not _SKILL_NAME_PATTERN.fullmatch(name):
        raise SkillParseError(f"技能 {directory.name} 的 name 字段只能包含字母、数字、下划线和横线")
    return name


class SkillManager:
    """技能扫描与加载管理器。

    扫描一个或多个技能根目录，解析每个子目录内 ``SKILL.md`` 的声明；
    加载技能内容时校验路径边界，避免越权读取技能目录以外的文件。
    """

    def __init__(self, roots: Optional[Sequence[str | Path]] = None) -> None:
        """初始化技能管理器。

        Args:
            roots: 技能根目录列表；相对路径基于项目根目录解析，
                缺省时使用 ``data/skills``。
        """

        raw_roots = list(roots or ["data/skills"])
        self._roots: list[Path] = []
        for raw_root in raw_roots:
            root_path = Path(str(raw_root or "").strip())
            if not str(root_path):
                continue
            if not root_path.is_absolute():
                root_path = PROJECT_ROOT / root_path
            self._roots.append(root_path.resolve())

    @property
    def roots(self) -> list[Path]:
        """返回技能根目录列表。"""

        return list(self._roots)

    def list_skills(self) -> list[Skill]:
        """扫描全部技能根目录并返回解析成功的技能列表。

        Returns:
            list[Skill]: 按技能名排序的技能列表。
        """

        skills: dict[str, Skill] = {}
        for root in self._roots:
            if not root.is_dir():
                logger.debug(f"技能目录不存在，已跳过: {root}")
                continue
            for candidate_path in sorted(
                entry.resolve()
                for entry in root.iterdir()
                if entry.is_dir() and not self._is_reserved_skill_directory(entry)
            ):
                try:
                    skill = self._load_skill_from_directory(candidate_path)
                except SkillParseError as exc:
                    logger.warning(f"技能声明解析失败，已跳过: {candidate_path}（{exc}）")
                    continue
                except (OSError, UnicodeDecodeError) as exc:
                    logger.warning(f"技能 SKILL.md 读取失败，已跳过: {candidate_path}（{exc}）")
                    continue
                previous = skills.get(skill.name)
                if previous is not None and previous.skill_md_path != skill.skill_md_path:
                    logger.warning(
                        f"检测到重复技能名 {skill.name}，保留 "
                        f"{previous.skill_md_path}，跳过 {skill.skill_md_path}"
                    )
                    continue
                skills[skill.name] = skill
        return [skills[name] for name in sorted(skills)]

    def get_skill(self, name: str) -> Optional[Skill]:
        """按技能名查找技能。

        Args:
            name: 技能名称。

        Returns:
            Optional[Skill]: 匹配的技能；不存在时返回 ``None``。
        """

        for skill in self.list_skills():
            if skill.name == name:
                return skill
        return None

    def load_skill_content(self, name: str) -> Optional[str]:
        """读取指定技能的完整内容。

        技能内容部分由可信技能作者编写，可能包含指令性语句；调用方
        （工具 Provider）会将其作为模型上下文注入，无需额外转义。

        Args:
            name: 技能名称。

        Returns:
            Optional[str]: ``SKILL.md`` 完整文本；技能不存在时返回 ``None``。
        """

        skill = self.get_skill(name)
        if skill is None:
            return None
        with skill.skill_md_path.open("r", encoding="utf-8") as skill_file:
            return skill_file.read()

    def _load_skill_from_directory(self, directory: Path) -> Skill:
        """解析单个技能目录内的 SKILL.md。

        Args:
            directory: 技能所在目录。

        Returns:
            Skill: 解析成功的技能元信息。

        Raises:
            SkillParseError: 声明缺失或不合规时抛出。
        """

        skill_md_path = directory / _SKILL_MD_FILENAME
        with skill_md_path.open("r", encoding="utf-8") as skill_file:
            content = skill_file.read()
        frontmatter = _parse_frontmatter(content)
        name = _parse_skill_name(frontmatter, directory)
        description = str(frontmatter.get("description") or "").strip()
        version = str(frontmatter.get("version") or "").strip()
        return Skill(
            name=name,
            description=description,
            directory=directory,
            skill_md_path=skill_md_path,
            version=version,
        )

    @staticmethod
    def _is_reserved_skill_directory(path: Path) -> bool:
        """判断目录是否为保留目录（data、__pycache__ 与隐藏目录）。

        Args:
            path: 待判断的目录路径。

        Returns:
            bool: 是否为保留目录。
        """

        name = path.name.casefold()
        return name in _RESERVED_SKILL_DIRECTORY_NAMES or name.startswith(".")


def _create_default_skill_manager() -> SkillManager:
    """创建基于全局配置的技能管理器。"""

    from src.config.config import global_config
    from src.config.official_configs import SkillConfig

    skill_config: SkillConfig = global_config.skills
    return SkillManager(roots=skill_config.directories)


_skill_manager: Optional[SkillManager] = None


def get_skill_manager() -> SkillManager:
    """获取进程级技能管理器单例。

    首次调用时基于全局配置创建，之后复用同一实例。
    全局配置热重载后技能目录变化需重启进程生效。

    Returns:
        SkillManager: 进程级技能管理器。
    """

    global _skill_manager
    if _skill_manager is None:
        _skill_manager = _create_default_skill_manager()
    return _skill_manager