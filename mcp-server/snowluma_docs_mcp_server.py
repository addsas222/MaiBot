"""
SnowLuma 适配器文档 MCP Server

索引 SnowLuma 适配器（MaiBot-SnowLuma-Adapter）的本地与远程文档，
提供文档列表、内容读取与搜索工具。
"""

from pathlib import Path

import httpx
try:
    # mcp 2.x：FastMCP 已并入 mcp.server.mcpserver.MCPServer，API 形态兼容
    from mcp.server.mcpserver import MCPServer as FastMCP
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP

mcp = FastMCP("SnowLuma 文档")

REPO_ROOT = Path(__file__).resolve().parents[1]
LOCAL_DIR = REPO_ROOT / "plugins" / "maibot-team_snowluma-adapter"

# 远程文档源：GitHub 仓库的 README 与 CHANGELOG
REMOTE_SOURCES = {
    "README.md": "https://raw.githubusercontent.com/Mai-with-u/MaiBot-SnowLuma-Adapter/main/README.md",
    "CHANGELOG.md": "https://raw.githubusercontent.com/Mai-with-u/MaiBot-SnowLuma-Adapter/main/CHANGELOG.md",
}

LOCAL_FILES = [
    "README.md",
    "CHANGELOG.md",
]


def _read_local(path: str) -> str | None:
    """读取本地插件目录内的文档。"""
    full_path = LOCAL_DIR / path
    try:
        return full_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


async def _get_remote(path: str) -> str | None:
    """抓取远程 GitHub 文档。"""
    url = REMOTE_SOURCES.get(path)
    if url is None:
        return None
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(url)
        if resp.status_code == 200:
            return resp.text
    except httpx.HTTPError:
        return None
    return None


async def _get_doc(path: str) -> str | None:
    """优先本地、回退远程获取文档内容。"""
    content = _read_local(path)
    if content is None or not content.strip():
        content = await _get_remote(path)
    return content


@mcp.tool()
async def snowluma_list_docs() -> str:
    """列出 SnowLuma 适配器可用的文档列表。"""
    out = ["# SnowLuma 适配器文档\n"]
    for path in sorted(set(LOCAL_FILES) | set(REMOTE_SOURCES)):
        content = _read_local(path)
        source = "本地" if content and content.strip() else "远程"
        first_line = (content or "").strip().split("\n")[0][:100] if content else ""
        out.append(f"- `{path}`（来源: {source}）{first_line}")
    return "\n".join(out)


@mcp.tool()
async def snowluma_get_doc(path: str) -> str:
    """获取 SnowLuma 适配器文档完整内容。路径通过 snowluma_list_docs 获取。

    Args:
        path: 文档文件名，如 "README.md"、"CHANGELOG.md"
    """
    if path not in LOCAL_FILES and path not in REMOTE_SOURCES:
        return f"无法获取文档: {path}\n可用文档：\n  - README.md\n  - CHANGELOG.md"

    content = await _get_doc(path)
    if content is None:
        return f"无法获取文档: {path}\n本地与远程均不可用。"
    return content


@mcp.tool()
async def snowluma_search(query: str, max_results: int = 5) -> str:
    """在 SnowLuma 适配器文档中搜索关键词。

    Args:
        query: 搜索关键词，如 "私聊"、"工具"、"配置"、"WebSocket"
        max_results: 最大返回文档数，默认 5
    """
    q = query.lower()
    results = []
    for path in sorted(set(LOCAL_FILES) | set(REMOTE_SOURCES)):
        content = await _get_doc(path)
        if not content or q not in content.lower():
            continue
        lines = [ln.strip() for ln in content.splitlines() if q in ln.lower()]
        snippet = lines[0][:150] if lines else ""
        results.append((path, snippet))

    if not results:
        return f"未找到与「{query}」相关的文档。\n\n可尝试：私聊、工具、配置、WebSocket、连接"

    out = [f"# 搜索「{query}」结果\n"]
    for path, snippet in results[:max_results]:
        out.append(f"## `{path}`")
        if snippet:
            out.append(f"- {snippet}")
        out.append("")
    out.append("使用 `snowluma_get_doc` 查看完整文档。")
    return "\n".join(out)


@mcp.resource("snowluma://docs")
async def snowluma_resource() -> str:
    """SnowLuma 文档列表资源"""
    return "\n".join(f"- `{p}`" for p in sorted(set(LOCAL_FILES) | set(REMOTE_SOURCES)))


if __name__ == "__main__":
    mcp.run()
