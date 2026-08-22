from pathlib import Path

import pytest

from src.core.tooling import ToolAvailabilityContext, ToolExecutionContext, ToolInvocation
from src.skills.manager import SkillManager
from src.skills.provider import SkillToolProvider


def _build_manager(tmp_path: Path, skill_name: str = "web-search", content: str | None = None) -> SkillManager:
    (tmp_path / skill_name).mkdir()
    skill_content = content or (
        "---\n"
        f"name: {skill_name}\n"
        "description: 实时搜索工具。\n"
        "---\n"
        "# 使用说明\n"
    )
    (tmp_path / skill_name / "SKILL.md").write_text(skill_content, encoding="utf-8")
    return SkillManager(roots=[tmp_path])


@pytest.mark.asyncio
async def test_list_tools_exposes_one_tool_per_skill(tmp_path: Path) -> None:
    provider = SkillToolProvider(_build_manager(tmp_path))
    specs = await provider.list_tools(None)
    assert len(specs) == 1
    spec = specs[0]
    assert spec.name == "skill_web-search"
    assert spec.provider_name == "skills"
    assert spec.parameters_schema == {"type": "object", "properties": {}, "additionalProperties": False}
    assert "实时搜索工具" in spec.description


@pytest.mark.asyncio
async def test_list_tools_uses_availability_context_signature(tmp_path: Path) -> None:
    provider = SkillToolProvider(_build_manager(tmp_path))
    context = ToolAvailabilityContext(session_id="session-1")
    assert await provider.list_tools(context)


@pytest.mark.asyncio
async def test_list_tools_filters_disabled_skills(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = SkillToolProvider(_build_manager(tmp_path))

    class _FakeSkillsConfig:
        disabled_skills: list[str] = ["web-search"]

    class _FakeGlobalConfig:
        skills = _FakeSkillsConfig()

    monkeypatch.setattr("src.config.config.global_config", _FakeGlobalConfig())
    specs = await provider.list_tools(None)
    assert specs == []

    class _EmptySkillsConfig:
        disabled_skills: list[str] = []

    monkeypatch.setattr(_FakeGlobalConfig, "skills", _EmptySkillsConfig())
    specs = await provider.list_tools(None)
    assert [spec.name for spec in specs] == ["skill_web-search"]


@pytest.mark.asyncio
async def test_invoke_loads_skill_content(tmp_path: Path) -> None:
    provider = SkillToolProvider(_build_manager(tmp_path))
    result = await provider.invoke(
        ToolInvocation(tool_name="skill_web-search"),
        ToolExecutionContext(session_id="session-1"),
    )
    assert result.success
    assert "name: web-search" in result.content
    assert "实时搜索工具" in result.content


@pytest.mark.asyncio
async def test_invoke_unknown_skill_returns_error(tmp_path: Path) -> None:
    provider = SkillToolProvider(_build_manager(tmp_path))
    result = await provider.invoke(ToolInvocation(tool_name="skill_missing-skill"), None)
    assert not result.success
    assert "未找到技能" in result.error_message


@pytest.mark.asyncio
async def test_invoke_unprefixed_tool_name_returns_error(tmp_path: Path) -> None:
    provider = SkillToolProvider(_build_manager(tmp_path))
    result = await provider.invoke(ToolInvocation(tool_name="web-search"), None)
    assert not result.success
    assert "格式不合法" in result.error_message


@pytest.mark.asyncio
async def test_invoke_truncates_oversized_skill_content(tmp_path: Path) -> None:
    provider = SkillToolProvider(_build_manager(tmp_path))
    content_limit = provider._get_max_content_length()
    oversized_content = (
        "---\nname: web-search\ndescription: desc\n---\n"
        + "正文内容" * (content_limit + 100)
    )
    (tmp_path / "oversized").mkdir()
    provider = SkillToolProvider(
        _build_manager(tmp_path / "oversized", content=oversized_content)
    )
    result = await provider.invoke(ToolInvocation(tool_name="skill_web-search"), None)
    assert result.success
    assert len(result.content) <= content_limit + 20
    assert "已截断" in result.content


@pytest.mark.asyncio
async def test_invoke_empty_file_skill_is_treated_as_missing(tmp_path: Path) -> None:
    provider = SkillToolProvider(_build_manager(tmp_path, content=""))
    (tmp_path / "web-search" / "SKILL.md").write_text("", encoding="utf-8")
    result = await provider.invoke(ToolInvocation(tool_name="skill_web-search"), None)
    assert not result.success
    assert "未找到技能" in result.error_message


@pytest.mark.asyncio
async def test_close_is_noop(tmp_path: Path) -> None:
    provider = SkillToolProvider(_build_manager(tmp_path))
    assert await provider.close() is None