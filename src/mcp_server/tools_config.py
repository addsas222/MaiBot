"""配置与插件管理类 MCP 工具。

配置读取/修改复用 ``src.config.config`` 的落盘 + 热重载链路；
插件启停遵循插件 config.toml 的 ``[plugin].enabled`` 真相源约定。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import tomlkit

from src.config.config import BOT_CONFIG_PATH, CONFIG_VERSION, config_manager, write_config_to_file
from src.plugin_runtime.integration import get_plugin_runtime_manager

CONFIG_SECTIONS: tuple[str, ...] = (
    "bot",
    "chat",
    "debug",
    "log",
    "maim_message",
    "webui",
    "database",
    "mcp",
    "plugin",
    "plugin_runtime",
)


def _find_plugin_path_by_id(plugin_id: str) -> Optional[Path]:
    """在 plugins 目录下按插件 ID 查找插件根目录（读取 _manifest.json）。"""

    plugins_dir = Path("plugins")
    if not plugins_dir.exists():
        return None
    normalized_plugin_id = str(plugin_id or "").strip().casefold()
    if not normalized_plugin_id:
        return None

    for plugin_path in plugins_dir.iterdir():
        if not plugin_path.is_dir():
            continue
        name = plugin_path.name.casefold()
        if name in {"data", "__pycache__"} or name.startswith("."):
            continue
        manifest_path = plugin_path / "_manifest.json"
        if not manifest_path.is_file():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if str(manifest.get("id") or "").strip().casefold() == normalized_plugin_id:
            return plugin_path
    return None


def register_config_tools(mcp: Any) -> None:
    """注册配置与插件管理类工具。"""

    @mcp.tool()
    def read_config_section(section: str) -> dict[str, Any]:
        """读取麦麦 bot 配置的指定段落。

        Args:
            section: 配置段落名，可选 bot / chat / debug / log / maim_message / webui /
                database / mcp / plugin / plugin_runtime。
        """
        section = str(section or "").strip()
        if section not in CONFIG_SECTIONS:
            return {"error": f"配置段不存在，可选: {', '.join(CONFIG_SECTIONS)}"}
        cfg = config_manager.get_global_config()
        value = getattr(cfg, section)
        return json.loads(value.model_dump_json())

    @mcp.tool()
    async def update_config_value(path: str, value: str) -> dict[str, Any]:
        """修改麦麦 bot 配置并落盘热重载（危险操作，请谨慎）。

        Args:
            path: 点分路径，如 bot.nickname / mcp.server.port。
            value: 目标值，必须是合法 JSON（如 "新名字"、8765、{"enable": true}）。
        """
        parts = [part for part in str(path or "").split(".") if part]
        if not parts:
            return {"success": False, "error": "path 不能为空"}
        try:
            parsed_value = json.loads(value)
        except json.JSONDecodeError:
            return {"success": False, "error": "value 必须是合法 JSON"}

        cfg = config_manager.get_global_config()
        current: Any = cfg
        for part in parts[:-1]:
            current = getattr(current, part, None)
            if current is None:
                return {"success": False, "error": f"配置路径不存在: {path}"}
        try:
            setattr(current, parts[-1], parsed_value)
        except (AttributeError, ValueError) as exc:
            return {"success": False, "error": f"配置修改失败: {exc}"}

        write_config_to_file(cfg, BOT_CONFIG_PATH, CONFIG_VERSION)
        reloaded = await config_manager.reload_config(changed_scopes=("bot",))
        return {
            "success": reloaded,
            "path": path,
            "reload_revision": config_manager.reload_revision,
        }

    @mcp.tool()
    def list_plugins() -> list[dict[str, Any]]:
        """列出麦麦已安装插件及其加载状态、版本与失败原因。"""
        manager = get_plugin_runtime_manager()
        statuses = manager.get_plugin_load_statuses()
        failure_reasons = manager.get_plugin_load_failure_reasons()
        versions: dict[str, str] = {}
        for supervisor in manager.supervisors:
            versions.update(supervisor.get_loaded_plugin_versions())
        plugin_ids = sorted(set(statuses) | set(versions))
        return [
            {
                "plugin_id": plugin_id,
                "status": statuses.get(plugin_id, "unknown"),
                "version": versions.get(plugin_id, ""),
                "failure_reason": failure_reasons.get(plugin_id, ""),
            }
            for plugin_id in plugin_ids
        ]

    @mcp.tool()
    def get_plugin_config(plugin_id: str) -> dict[str, Any]:
        """读取指定插件的 config.toml 配置内容。

        Args:
            plugin_id: 插件 ID。
        """
        plugin_path = _find_plugin_path_by_id(plugin_id)
        if plugin_path is None:
            return {"error": f"未找到插件: {plugin_id}"}
        config_path = plugin_path / "config.toml"
        if not config_path.exists():
            return {"error": f"插件 {plugin_id} 没有配置文件"}
        try:
            return tomlkit.loads(config_path.read_text(encoding="utf-8")).unwrap()
        except (OSError, tomlkit.exceptions.ParseError) as exc:
            return {"error": f"插件配置解析失败: {exc}"}

    @mcp.tool()
    def set_plugin_enabled(plugin_id: str, enabled: bool) -> dict[str, Any]:
        """启用或禁用指定插件（写入插件 config.toml，运行时自动同步）。

        Args:
            plugin_id: 插件 ID。
            enabled: True 启用，False 禁用。
        """
        plugin_path = _find_plugin_path_by_id(plugin_id)
        if plugin_path is None:
            return {"success": False, "error": f"未找到插件: {plugin_id}"}
        config_path = plugin_path / "config.toml"
        if not config_path.exists():
            return {"success": False, "error": f"插件 {plugin_id} 没有配置文件"}
        try:
            document = tomlkit.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, tomlkit.exceptions.ParseError) as exc:
            return {"success": False, "error": f"插件配置解析失败: {exc}"}
        plugin_section = document.get("plugin")
        if plugin_section is None:
            plugin_section = tomlkit.table()
            document["plugin"] = plugin_section
        plugin_section["enabled"] = bool(enabled)
        try:
            config_path.write_text(tomlkit.dumps(document), encoding="utf-8")
        except OSError as exc:
            return {"success": False, "error": f"插件配置写入失败: {exc}"}
        return {"success": True, "plugin_id": plugin_id, "enabled": bool(enabled)}

    @mcp.tool()
    async def reload_plugin(plugin_id: str) -> dict[str, Any]:
        """热重载指定插件。

        Args:
            plugin_id: 插件 ID。
        """
        manager = get_plugin_runtime_manager()
        success = await manager.reload_plugins_globally([plugin_id], reason="mcp")
        return {"success": success, "plugin_id": plugin_id}
