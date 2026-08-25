"""
ai-agent-book 书籍 MCP Server

将《AI Agent 开发》一书（mcp-server/ai-agent-book/book/）按章节索引，
提供章节列表、章节内容读取与关键词搜索工具。
"""

from pathlib import Path

try:
    # mcp 2.x：FastMCP 已并入 mcp.server.mcpserver.MCPServer，API 形态兼容
    from mcp.server.mcpserver import MCPServer as FastMCP
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP

mcp = FastMCP("AI Agent 书籍")

BOOK_DIR = Path(__file__).resolve().parent / "ai-agent-book" / "book"

CHAPTER_FILES = [
    "introduction.md",
    "chapter1.md",
    "chapter2.md",
    "chapter3.md",
    "chapter4.md",
    "chapter5.md",
    "chapter6.md",
    "chapter7.md",
    "chapter8.md",
    "chapter9.md",
    "chapter10.md",
    "afterword.md",
    "reference-answers.md",
]

CHAPTER_TITLES = {
    "introduction.md": "引言",
    "chapter1.md": "第一章",
    "chapter2.md": "第二章",
    "chapter3.md": "第三章",
    "chapter4.md": "第四章",
    "chapter5.md": "第五章",
    "chapter6.md": "第六章",
    "chapter7.md": "第七章",
    "chapter8.md": "第八章",
    "chapter9.md": "第九章",
    "chapter10.md": "第十章",
    "afterword.md": "后记",
    "reference-answers.md": "参考答案",
}

MAX_CONTENT_LENGTH = 24000  # 单次返回的章节内容上限（字符）


def _read_chapter(path: str) -> str | None:
    """读取书籍目录内相对路径的 Markdown 内容。"""
    full_path = BOOK_DIR / path
    try:
        return full_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _extract_sections(content: str) -> list[str]:
    """提取 Markdown 中的二级标题（##），作为小节索引。"""
    sections = []
    for line in content.splitlines():
        if line.startswith("## "):
            sections.append(line[3:].strip())
    return sections


@mcp.tool()
async def book_list_chapters() -> str:
    """列出《AI Agent 开发》的全部章节及每章小节。"""
    out = ["# 《AI Agent 开发》章节结构\n"]
    for path in CHAPTER_FILES:
        content = _read_chapter(path) or ""
        sections = _extract_sections(content)
        title = CHAPTER_TITLES.get(path, path)
        out.append(f"## {title} (`{path}`)")
        for sec in sections[:30]:
            out.append(f"- {sec}")
        if len(sections) > 30:
            out.append(f"- …（共 {len(sections)} 个小节）")
        out.append("")
    return "\n".join(out)


@mcp.tool()
async def book_get_chapter(path: str, max_length: int = MAX_CONTENT_LENGTH) -> str:
    """获取指定章节的完整内容。章节路径通过 book_list_chapters 获取。

    Args:
        path: 章节文件名，如 "chapter1.md"、"introduction.md"
        max_length: 返回内容最大字符数，默认 24000
    """
    if path not in CHAPTER_FILES:
        return f"无法获取章节: {path}\n可用章节：\n" + "\n".join(f"  - {p}" for p in CHAPTER_FILES)

    content = _read_chapter(path)
    if content is None:
        return f"无法获取章节: {path}\n文件不存在。"

    if len(content) > max_length:
        return content[:max_length] + f"\n\n...(内容过长，已截断前 {max_length} 字符)"
    return content


@mcp.tool()
async def book_search(query: str, max_results: int = 5) -> str:
    """在《AI Agent 开发》全书中搜索关键词，返回命中的章节及上下文片段。

    Args:
        query: 搜索关键词，如 "Agent"、"记忆"、"工具"、"规划"
        max_results: 最大返回章节数，默认 5
    """
    q = query.lower()
    results = []
    for path in CHAPTER_FILES:
        content = _read_chapter(path) or ""
        if q not in content.lower():
            continue
        lines = content.splitlines()
        hits = [ln.strip()[:150] for ln in lines if q in ln.lower()][:3]
        results.append((path, hits))

    if not results:
        return f"未找到与「{query}」相关的章节。\n\n可尝试：Agent、LLM、记忆、工具、规划、多模态、评估"

    out = [f"# 搜索「{query}」结果\n"]
    for path, hits in results[:max_results]:
        out.append(f"## {CHAPTER_TITLES.get(path, path)} (`{path}`)")
        for hit in hits:
            out.append(f"- {hit}")
        out.append("")
    out.append(f"共 {len(results)} 章命中，显示前 {min(max_results, len(results))} 章。")
    out.append("使用 `book_get_chapter` 查看完整章节。")
    return "\n".join(out)


@mcp.resource("ai-agent-book://chapters")
async def book_resource() -> str:
    """书籍章节索引资源"""
    lines = ["# 《AI Agent 开发》章节\n"]
    for path in CHAPTER_FILES:
        title = CHAPTER_TITLES.get(path, path)
        lines.append(f"- {title}: `{path}`")
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()
