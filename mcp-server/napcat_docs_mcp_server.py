"""
NapCat 适配器文档 MCP Server

索引 NapCat 适配器（MaiBot-Napcat-Adapter）的本地文档目录，
提供文档列表、内容读取与搜索工具。
"""

from pathlib import Path

try:
    # mcp 2.x：FastMCP 已并入 mcp.server.mcpserver.MCPServer，API 形态兼容
    from mcp.server.mcpserver import MCPServer as FastMCP
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP

mcp = FastMCP("NapCat 文档")

REPO_ROOT = Path(__file__).resolve().parents[1]
DOC_DIR = REPO_ROOT / "plugins" / "MaiBot-Napcat-Adapter" / "docs"

LOCAL_FILES = [
    "CHANGELOG.md",
    "account-api.md",
    "file-api.md",
    "group-api.md",
    "message-api.md",
    "system-api.md",
    "typed-api.md",
    "verification.md",
]

MAX_CONTENT_LENGTH = 24000


def _read_doc(path: str) -> str | None:
    """读取 NapCat 适配器 docs 目录内的文档。"""
    plugin_dir = REPO_ROOT / "plugins" / "MaiBot-Napcat-Adapter"
    if path in ("README.md", "CHANGELOG.md"):
        full_path = plugin_dir / path
    else:
        full_path = DOC_DIR / path
    try:
        return full_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


@mcp.tool()
async def napcat_list_docs() -> str:
    """列出 NapCat 适配器可用的文档列表及简介。"""
    out = ["# NapCat 适配器文档\n"]
    for path in LOCAL_FILES:
        content = _read_doc(path)
        if content is None:
            out.append(f"- `{path}`（不可用）")
            continue
        first_line = content.strip().split("\n")[0][:120]
        out.append(f"- `{path}` — {first_line}")
    return "\n".join(out)


@mcp.tool()
async def napcat_get_doc(path: str, max_length: int = MAX_CONTENT_LENGTH) -> str:
    """获取 NapCat 适配器文档完整内容。路径通过 napcat_list_docs 获取。

    Args:
        path: 文档文件名，如 "message-api.md"、"group-api.md"、"README.md"
        max_length: 返回内容最大字符数，默认 24000
    """
    if path not in LOCAL_FILES:
        return f"无法获取文档: {path}\n可用文档：\n" + "\n".join(f"  - {p}" for p in LOCAL_FILES)

    content = _read_doc(path)
    if content is None:
        return f"无法获取文档: {path}\n文件不存在。"

    if len(content) > max_length:
        return content[:max_length] + f"\n\n...(内容过长，已截断前 {max_length} 字符)"
    return content


@mcp.tool()
async def napcat_search(query: str, max_results: int = 5) -> str:
    """在 NapCat 适配器文档中搜索关键词。

    Args:
        query: 搜索关键词，如 "消息"、"群"、"图片"、"接口"、"验证"
        max_results: 最大返回文档数，默认 5
    """
    q = query.lower()
    results = []
    for path in LOCAL_FILES:
        content = _read_doc(path)
        if not content or q not in content.lower():
            continue
        lines = [ln.strip() for ln in content.splitlines() if q in ln.lower()]
        snippet = lines[0][:150] if lines else ""
        results.append((path, snippet))

    if not results:
        return f"未找到与「{query}」相关的文档。\n\n可尝试：消息、群、图片、接口、验证、好友"

    out = [f"# 搜索「{query}」结果\n"]
    for path, snippet in results[:max_results]:
        out.append(f"## `{path}`")
        if snippet:
            out.append(f"- {snippet}")
        out.append("")
    out.append("使用 `napcat_get_doc` 查看完整文档。")
    return "\n".join(out)


@mcp.resource("napcat://docs")
async def napcat_resource() -> str:
    """NapCat 文档列表资源"""
    return "\n".join(f"- `{p}`" for p in LOCAL_FILES)


if __name__ == "__main__":
    mcp.run()
