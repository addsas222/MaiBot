from pathlib import Path

from src.skills.manager import Skill, SkillManager, _parse_frontmatter, _parse_skill_name


def test_parse_frontmatter_extracts_single_line_fields() -> None:
    content = (
        "---\n"
        "name: web-search\n"
        "description: 实时搜索工具，支持网页搜索与 URL 内容提取。\n"
        "version: 3.0.1\n"
        "---\n"
        "\n"
        "## Overview\n"
        "正文内容"
    )
    fields = _parse_frontmatter(content)
    assert fields == {"name": "web-search", "description": "实时搜索工具，支持网页搜索与 URL 内容提取。", "version": "3.0.1"}


def test_parse_frontmatter_strips_quotes() -> None:
    content = '---\nname: "quoted-skill"\ndescription: \'带引号描述\'\n---\n正文'
    assert _parse_frontmatter(content) == {"name": "quoted-skill", "description": "带引号描述"}


def test_parse_frontmatter_returns_empty_without_header() -> None:
    assert _parse_frontmatter("没有任何 frontmatter 的普通文本") == {}
    assert _parse_frontmatter("") == {}


def test_parse_skill_name_rejects_invalid_names() -> None:
    directory = Path("/tmp/skill-dir")
    assert _parse_skill_name({"name": "web-search"}, directory) == "web-search"
    for invalid in ["", "  ", "a/b", "a\\b", "..", ".hidden", "a" * 65]:
        try:
            _parse_skill_name({"name": invalid}, directory)
        except ValueError:
            continue
        raise AssertionError(f"应拒绝技能名 {invalid!r}")


def test_list_skills_parses_directories(tmp_path: Path) -> None:
    (tmp_path / "web-search").mkdir()
    (tmp_path / "web-search" / "SKILL.md").write_text(
        "---\nname: web-search\ndescription: 实时搜索引擎。\n---\n# 使用说明\n正文。",
        encoding="utf-8",
    )
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "SKILL.md").write_text(
        "---\nname: reserved\ndescription: 保留目录应被跳过。\n---\n正文。",
        encoding="utf-8",
    )
    (tmp_path / ".hidden-skill").mkdir()
    (tmp_path / ".hidden-skill" / "SKILL.md").write_text(
        "---\nname: hidden\ndescription: 隐藏目录应被跳过。\n---\n正文。",
        encoding="utf-8",
    )

    manager = SkillManager(roots=[tmp_path])

    skills = manager.list_skills()
    assert [skill.name for skill in skills] == ["web-search"]
    assert skills[0].description == "实时搜索引擎。"
    assert skills[0].skill_md_path == (tmp_path / "web-search" / "SKILL.md").resolve()


def test_list_skills_skips_directories_without_skill_md(tmp_path: Path) -> None:
    (tmp_path / "no-skill-md").mkdir()
    (tmp_path / "no-skill-md" / "notes.txt").write_text("没有 SKILL.md 的目录", encoding="utf-8")

    manager = SkillManager(roots=[tmp_path])
    assert manager.list_skills() == []


def test_get_skill_returns_none_for_missing(tmp_path: Path) -> None:
    (tmp_path / "web-search").mkdir()
    (tmp_path / "web-search" / "SKILL.md").write_text(
        "---\nname: web-search\ndescription: desc\n---\n正文。",
        encoding="utf-8",
    )
    manager = SkillManager(roots=[tmp_path])
    assert manager.get_skill("web-search") is not None
    assert manager.get_skill("not-exist") is None


def test_load_skill_content_reads_full_markdown(tmp_path: Path) -> None:
    (tmp_path / "web-search").mkdir()
    content = "---\nname: web-search\ndescription: desc\n---\n## 使用\n完整指令内容。"
    (tmp_path / "web-search" / "SKILL.md").write_text(content, encoding="utf-8")

    manager = SkillManager(roots=[tmp_path])
    assert manager.load_skill_content("web-search") == content
    assert manager.load_skill_content("not-exist") is None


def test_skill_name_mismatch_with_directory_name(tmp_path: Path) -> None:
    (tmp_path / "dir-name-a").mkdir()
    (tmp_path / "dir-name-a" / "SKILL.md").write_text(
        "---\nname: declared-name\ndescription: desc\n---\n正文。",
        encoding="utf-8",
    )
    manager = SkillManager(roots=[tmp_path])
    skills = manager.list_skills()
    assert [skill.name for skill in skills] == ["declared-name"]
    assert skills[0].directory.name == "dir-name-a"


def test_reserved_skill_directory_check() -> None:
    assert SkillManager._is_reserved_skill_directory(Path("/tmp/data"))
    assert SkillManager._is_reserved_skill_directory(Path("/tmp/.hidden"))
    assert SkillManager._is_reserved_skill_directory(Path("/tmp/__pycache__"))
    assert not SkillManager._is_reserved_skill_directory(Path("/tmp/web-search"))


def test_skill_dataclass_fields() -> None:
    skill = Skill(
        name="web-search",
        description="desc",
        directory=Path("/tmp/web-search"),
        skill_md_path=Path("/tmp/web-search/SKILL.md"),
        version="3.0.1",
    )
    assert skill.version == "3.0.1"
    assert skill.name == "web-search"