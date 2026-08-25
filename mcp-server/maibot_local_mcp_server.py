"""
MaiBot 文档 MCP Server

- 本地：将仓库内的 MaiBot 文档（README、docs/、changelogs/、AGENTS.md 等）打包索引，离线可用。
- 远程：抓取 docs.mai-mai.org 的 llms-full.txt（82 个页面）按 URL 分段索引，联网可用。
"""

from pathlib import Path
import re

import httpx
try:
    # mcp 2.x：FastMCP 已并入 mcp.server.mcpserver.MCPServer，API 形态兼容
    from mcp.server.mcpserver import MCPServer as FastMCP
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP

mcp = FastMCP("MaiBot 文档")

REPO_ROOT = Path(__file__).resolve().parents[1]

LLMS_URL = "https://docs.mai-mai.org/llms-full.txt"

DOC_SOURCES = {
    "README": ["README.md"],
    "开发指南": [
        "docs/CONTRIBUTE.md",
        "docs/README_CN.md",
        "docs/README_EN.md",
        "docs/i18n.md",
        "docs/minimal-cross-platform-plan.md",
        "docs/plugin_persistence.md",
    ],
    "开发规范": [
        "AGENTS.md",
        "CLAUDE.md",
        "docs/a_memorix_sync.md",
        "docs/crowdin_workflow_alignment_brief.md",
        "docs/github-actions-crowdin-workflow-report.md",
        "docs/responses_item_context_refactor/README.md",
    ],
    "更新日志": [
        "changelogs/changelog.md",
        "changelogs/changelog_dev.md",
    ],
    "法律条款": [
        "CODE_OF_CONDUCT.md",
        "EULA.md",
        "PRIVACY.md",
    ],
}


def _read_doc(path: str) -> str | None:
    """读取仓库内相对路径的 Markdown 文档内容。"""
    full_path = REPO_ROOT / path
    try:
        return full_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _build_index() -> list[dict]:
    """构建文档索引：分类、路径、标题、描述。"""
    index = []
    for category, paths in DOC_SOURCES.items():
        for path in paths:
            title = Path(path).stem
            if title in {"README", "README_CN", "README_EN"}:
                title = "README" if title == "README" else f"MaiBot {title.replace('README_', '')} 说明"
            else:
                title = title.replace("_", " ").replace("-", " ").title()
            index.append(
                {
                    "category": category,
                    "path": path,
                    "title": title,
                    "description": _read_doc(path).strip().split("\n")[0][:120]
                    if _read_doc(path) is not None
                    else "",
                }
            )
    return index


DOC_INDEX = _build_index()


@mcp.tool()
async def maibot_local_search(query: str, max_results: int = 5) -> str:
    """搜索打包在仓库内的 MaiBot 本地文档。支持关键词搜索，返回匹配的文档和摘要。

    Args:
        query: 搜索关键词，如 "MCP"、"插件"、"记忆"、"部署"、"配置"
        max_results: 最大返回结果数，默认 5
    """
    q = query.lower()
    results = []

    for doc in DOC_INDEX:
        content = _read_doc(doc["path"]) or ""
        score = 0
        if q in doc["title"].lower():
            score += 3
        if q in doc["category"].lower():
            score += 2
        if q in doc["path"].lower():
            score += 1
        if q in content.lower():
            score += 2
        if score > 0:
            results.append((score, doc))

    results.sort(key=lambda x: -x[0])

    if not results:
        return (
            f"未找到与「{query}」相关的文档。\n\n"
            "可尝试的搜索词：MCP、插件、记忆、部署、配置、WebUI、开发、更新"
        )

    out = []
    for _score, doc in results[:max_results]:
        out.append(f"### {doc['title']}")
        out.append(f"- **分类**: {doc['category']}")
        out.append(f"- **路径**: {doc['path']}")
        if doc["description"]:
            out.append(f"- **摘要**: {doc['description']}")
        out.append("")

    out.append("---")
    out.append(f"共找到 {len(results)} 条结果，显示前 {min(max_results, len(results))} 条。")
    out.append("使用 `maibot_local_get_doc` 工具查看完整文档内容。")

    return "\n".join(out)


@mcp.tool()
async def maibot_local_get_doc(path: str) -> str:
    """获取打包在仓库内的 MaiBot 本地文档完整内容。先通过 maibot_local_search 找到需要的文档路径。

    Args:
        path: 文档相对路径，如 "docs/CONTRIBUTE.md"、"changelogs/changelog.md"、"README.md"
    """
    if path not in {doc["path"] for doc in DOC_INDEX}:
        available = "\n".join(f"  - {doc['path']}" for doc in DOC_INDEX)
        return f"无法获取文档内容: {path}\n可用文档列表：\n{available}"

    content = _read_doc(path)
    if content is None:
        return f"无法获取文档内容: {path}\n文件不存在。"
    return content


@mcp.tool()
async def maibot_local_list_docs() -> str:
    """列出打包在仓库内的所有 MaiBot 本地文档分类和文件。"""
    out = ["# MaiBot 本地文档结构\n"]

    for category, paths in DOC_SOURCES.items():
        out.append(f"## {category} ({len(paths)} 个文件)")
        for path in paths:
            doc = next(d for d in DOC_INDEX if d["path"] == path)
            out.append(f"- {doc['title']} (`{path}`)")
        out.append("")

    out.append("---")
    out.append(f"共 {len(DOC_INDEX)} 个文档文件，{len(DOC_SOURCES)} 个分类")
    return "\n".join(out)


@mcp.resource("maibot-local://docs")
async def resource_doc_list() -> str:
    """列出所有本地文档分类"""
    lines = ["# MaiBot 本地文档分类\n"]
    for category, paths in DOC_SOURCES.items():
        lines.append(f"## {category} ({len(paths)} 个文件)")
        for path in paths:
            doc = next(d for d in DOC_INDEX if d["path"] == path)
            lines.append(f"- {doc['title']}: `{path}`")
    return "\n".join(lines)


_REMOTE_CACHE: dict | None = None
_REMOTE_ETAG = None


def _parse_llms_full(text: str) -> dict[str, str]:
    """把 llms-full.txt 按 `--- url: xxx.md ---` 分段解析为 {url: content}。"""
    parts = re.split(r"^---\s*$", text, flags=re.M)
    docs: dict[str, str] = {}
    for i in range(0, len(parts) - 1, 2):
        meta, content = parts[i], parts[i + 1]
        m = re.search(r"^url:\s*(/\S+\.md)\s*$", meta, flags=re.M)
        if m:
            docs[m.group(1)] = content.strip()
    return docs


async def _get_remote_docs() -> dict[str, str]:
    """懒加载并缓存远程文档索引。"""
    global _REMOTE_CACHE, _REMOTE_ETAG

    if _REMOTE_CACHE is None:
        headers = {"If-None-Match": _REMOTE_ETAG} if _REMOTE_ETAG else {}
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(LLMS_URL, headers=headers)
        if resp.status_code == 200:
            _REMOTE_ETAG = resp.headers.get("etag")
            _REMOTE_CACHE = _parse_llms_full(resp.text)
    return _REMOTE_CACHE or {}


@mcp.tool()
async def maibot_remote_list_docs() -> str:
    """列出 docs.mai-mai.org 远程文档的所有页面路径和标题。"""
    docs = await _get_remote_docs()
    if not docs:
        return "无法获取远程文档列表，请检查网络连接后重试。"
    lines = ["# MaiBot 远程文档（docs.mai-mai.org）\n"]
    for path in sorted(docs):
        lines.append(f"- `{path}`")
    lines.append("")
    lines.append(f"共 {len(docs)} 个页面")
    return "\n".join(lines)


@mcp.tool()
async def maibot_remote_search(query: str, max_results: int = 5) -> str:
    """在 docs.mai-mai.org 远程文档中按关键词搜索，返回匹配页面及摘要。

    Args:
        query: 搜索关键词，如 "MCP"、"插件"、"记忆"、"部署"、"配置"
        max_results: 最大返回结果数，默认 5
    """
    docs = await _get_remote_docs()
    if not docs:
        return "无法获取远程文档，请检查网络连接后重试。"

    q = query.lower()
    results = []
    for path, content in docs.items():
        score = 0
        if q in path.lower():
            score += 3
        if q in content[:2000].lower():
            score += 2
        if q in content.lower():
            score += 1
        if score > 0:
            lines = [ln for ln in content.splitlines() if ln.strip()]
            snippet = next((ln for ln in lines if q in ln.lower()), "")[:120]
            results.append((score, path, snippet))

    results.sort(key=lambda x: -x[0])

    if not results:
        return (
            f"未找到与「{query}」相关的远程文档。\n\n"
            "可尝试的搜索词：MCP、插件、记忆、部署、配置、WebUI、开发、更新"
        )

    out = []
    for _score, path, snippet in results[:max_results]:
        out.append(f"### `{path}`")
        if snippet:
            out.append(f"- **相关片段**: {snippet}")
        out.append("")

    out.append("---")
    out.append(f"共找到 {len(results)} 条结果，显示前 {min(max_results, len(results))} 条。")
    out.append("使用 `maibot_remote_get_doc` 工具查看完整文档内容。")

    return "\n".join(out)


@mcp.tool()
async def maibot_remote_get_doc(path: str) -> str:
    """获取 docs.mai-mai.org 远程文档页面的完整内容。先通过 maibot_remote_search 找到需要的页面路径。

    Args:
        path: 文档路径，如 "/manual/configuration/bot-config.md"
    """
    docs = await _get_remote_docs()
    if not docs:
        return "无法获取远程文档，请检查网络连接后重试。"

    content = docs.get(path)
    if content is None:
        available = "\n".join(f"  - {p}" for p in sorted(docs))
        return f"无法获取文档内容: {path}\n可用页面列表：\n{available}"
    return f"# 远程文档: {path}\n\n{content}"


if __name__ == "__main__":
    mcp.run()