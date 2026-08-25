"""
MaiBot 插件市场咨询 MCP Server

连接官方插件市场数据源（Mai-with-u/plugin-repo 的 plugins.json），
提供插件搜索、列表与详情（manifest / README）查询工具。
"""

import json
import re

import httpx
try:
    # mcp 2.x：FastMCP 已并入 mcp.server.mcpserver.MCPServer，API 形态兼容
    from mcp.server.mcpserver import MCPServer as FastMCP
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP

mcp = FastMCP("MaiBot 插件市场")

PLUGINS_JSON_URL = "https://raw.githubusercontent.com/Mai-with-u/plugin-repo/main/plugins.json"
GITHUB_RAW_PREFIX = "https://raw.githubusercontent.com"

# 缓存：插件索引
_plugins_cache: list[dict] | None = None
# 缓存：仓库详情 {repo_full_name: {"manifest": dict|None, "readme": str|None}}
_details_cache: dict[str, dict] = {}


def _repo_name_from_url(url: str) -> str | None:
    """从 GitHub 仓库 URL 提取 owner/repo。"""
    match = re.search(r"github\.com/([^/]+/[^/]+)", url)
    if match:
        return match.group(1)
    return None


async def _load_plugins() -> list[dict]:
    """懒加载插件索引。"""
    global _plugins_cache
    if _plugins_cache is None:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(PLUGINS_JSON_URL)
        resp.raise_for_status()
        _plugins_cache = json.loads(resp.text)
    return _plugins_cache


async def _fetch_repo_detail(repo: str) -> dict:
    """抓取仓库的 _manifest.json 与 README.md（带缓存）。"""
    if repo in _details_cache:
        return _details_cache[repo]

    detail: dict = {"manifest": None, "readme": None}
    async with httpx.AsyncClient(timeout=30) as client:
        for filename, key in (("_manifest.json", "manifest"), ("README.md", "readme")):
            url = f"{GITHUB_RAW_PREFIX}/{repo}/main/{filename}"
            try:
                resp = await client.get(url)
                if resp.status_code != 200:
                    resp = await client.get(f"{GITHUB_RAW_PREFIX}/{repo}/master/{filename}")
            except httpx.HTTPError:
                continue
            if resp.status_code == 200:
                if key == "manifest":
                    try:
                        detail[key] = json.loads(resp.text)
                    except json.JSONDecodeError:
                        detail[key] = None
                else:
                    detail[key] = resp.text[:12000]
    _details_cache[repo] = detail
    return detail


@mcp.tool()
async def market_search(query: str, max_results: int = 10) -> str:
    """在插件市场按关键词搜索插件（匹配插件 ID / 仓库名）。

    Args:
        query: 搜索关键词，如 "音乐"、"图片"、"新闻"、"管理员"、"napcat"
        max_results: 最大返回结果数，默认 10
    """
    plugins = await _load_plugins()
    q = query.lower()
    results = []
    for plugin in plugins:
        plugin_id = str(plugin.get("id") or "")
        repo_url = str(plugin.get("repositoryUrl") or "")
        repo = _repo_name_from_url(repo_url) or ""
        if q in plugin_id.lower() or q in repo.lower():
            results.append((plugin_id, repo_url))

    if not results:
        return f"未找到与「{query}」相关的插件。\n\n可尝试：music、image、news、admin、napcat、search"

    out = [f"# 插件市场搜索「{query}」结果（共 {len(results)} 个）\n"]
    for plugin_id, repo_url in results[:max_results]:
        out.append(f"- **{plugin_id}**")
        out.append(f"  - 仓库: {repo_url}")
        out.append("")
    out.append("使用 `market_get_plugin` 查看插件详情。")
    return "\n".join(out)


@mcp.tool()
async def market_list_plugins(limit: int = 20, offset: int = 0) -> str:
    """分页列出插件市场的全部插件。

    Args:
        limit: 每页数量，默认 20，最大 100
        offset: 起始偏移，默认 0
    """
    plugins = await _load_plugins()
    limit = max(1, min(limit, 100))
    page = plugins[offset : offset + limit]

    out = [f"# MaiBot 插件市场（共 {len(plugins)} 个插件）\n"]
    for plugin in page:
        plugin_id = str(plugin.get("id") or "")
        repo_url = str(plugin.get("repositoryUrl") or "")
        out.append(f"- **{plugin_id}** — {repo_url}")
    out.append("")
    out.append(f"显示第 {offset + 1}-{offset + len(page)} 个，共 {len(plugins)} 个。")
    out.append("使用 `market_search` 关键词搜索，`market_get_plugin` 查看详情。")
    return "\n".join(out)


@mcp.tool()
async def market_get_plugin(plugin_id: str) -> str:
    """查询插件市场的单个插件详情（manifest + README 摘要）。

    Args:
        plugin_id: 插件 ID，如 "maibot-team.snowluma-adapter"；可用 market_search 查找
    """
    plugins = await _load_plugins()
    plugin = next((p for p in plugins if p.get("id") == plugin_id), None)
    if plugin is None:
        return f"未找到插件: {plugin_id}\n可使用 market_search 搜索。"

    repo_url = str(plugin.get("repositoryUrl") or "")
    repo = _repo_name_from_url(repo_url)
    out = [f"# 插件 {plugin_id}\n", f"- **仓库**: {repo_url}"]

    if repo is None:
        return "\n".join(out)

    detail = await _fetch_repo_detail(repo)
    manifest = detail.get("manifest")
    if manifest:
        out.append(f"- **名称**: {manifest.get('name', '')}")
        out.append(f"- **版本**: {manifest.get('version', '')}")
        out.append(f"- **描述**: {manifest.get('description', '')}")
        out.append(f"- **作者**: {manifest.get('author', {}).get('name', '') if isinstance(manifest.get('author'), dict) else manifest.get('author', '')}")
        out.append(f"- **插件类型**: {manifest.get('plugin_type', '普通')}")
        capabilities = manifest.get("capabilities")
        if capabilities:
            out.append(f"- **能力**: {', '.join(capabilities)}")
        dependencies = manifest.get("dependencies")
        if dependencies:
            out.append(f"- **依赖**: {', '.join(str(d.get('name', '')) for d in dependencies if isinstance(d, dict))}")
        urls = manifest.get("urls", {})
        if urls.get("documentation"):
            out.append(f"- **文档**: {urls['documentation']}")

    readme = detail.get("readme")
    if readme:
        out.append("\n## README 摘要")
        out.append(readme[:3000])

    out.append("\n> 完整文档请访问插件仓库。")
    return "\n".join(out)


@mcp.resource("maibot-market://plugins")
async def market_resource() -> str:
    """插件市场索引资源"""
    plugins = await _load_plugins()
    lines = [f"# MaiBot 插件市场（共 {len(plugins)} 个插件）"]
    for plugin in plugins[:100]:
        lines.append(f"- {plugin.get('id', '')}: {plugin.get('repositoryUrl', '')}")
    if len(plugins) > 100:
        lines.append(f"...（其余 {len(plugins) - 100} 个请使用 market_list_plugins）")
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()
