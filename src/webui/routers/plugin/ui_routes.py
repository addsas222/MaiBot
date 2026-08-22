"""插件自带 WebUI 页面的路由。

约定：插件目录下存在 `webui/index.html` 即视为该插件提供了 WebUI 页面，
无需在 _manifest.json 中额外声明。
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from src.common.logger import get_logger
from src.plugin_runtime.runner.manifest_validator import is_reserved_plugin_directory
from src.webui.dependencies import require_auth

from .support import (
    _resolve_safe_plugin_directory,
    get_plugins_dir,
    load_manifest_json,
    resolve_plugin_file_path,
)

logger = get_logger("webui.plugin_ui_routes")

router = APIRouter(prefix="/ui", dependencies=[Depends(require_auth)])

# 插件 WebUI 页面所在的子目录名与入口文件名
PLUGIN_WEBUI_DIRNAME = "webui"
PLUGIN_WEBUI_INDEX = "index.html"


def _iter_plugin_roots() -> List[Path]:
    """返回所有插件根目录：第三方插件目录与内置插件目录。"""
    roots = [get_plugins_dir()]
    builtin_root = Path("src/plugins/built_in")
    if builtin_root.is_dir():
        roots.append(builtin_root.resolve())
    return roots


def _collect_webui_plugins() -> Dict[str, Dict[str, Any]]:
    """扫描所有插件根目录，收集提供了 WebUI 页面的插件。

    返回 plugin_id -> {"webui_root": Path, "name": str, "description": str}。
    """
    collected: Dict[str, Dict[str, Any]] = {}

    for plugins_root in _iter_plugin_roots():
        for entry in plugins_root.iterdir():
            if is_reserved_plugin_directory(entry):
                continue

            plugin_path = _resolve_safe_plugin_directory(entry, plugins_root, strict=False)
            if plugin_path is None:
                continue

            # plugin_path 已解析并确认在插件根目录内，拼接固定子目录名不会越界；
            # 符号链接目录直接跳过，避免把插件目录外的内容暴露出去。
            webui_root = plugin_path / PLUGIN_WEBUI_DIRNAME
            if webui_root.is_symlink() or not webui_root.is_dir():
                continue
            if not (webui_root / PLUGIN_WEBUI_INDEX).is_file():
                continue

            manifest = load_manifest_json(resolve_plugin_file_path(plugin_path, "_manifest.json"))
            if manifest is None:
                logger.warning(f"插件目录 {plugin_path.name} 提供了 WebUI 页面但缺少可读的 _manifest.json，跳过")
                continue

            plugin_id = str(manifest.get("id") or "").strip()
            if not plugin_id:
                logger.warning(f"插件目录 {plugin_path.name} 的 _manifest.json 缺少 id，跳过")
                continue

            if plugin_id in collected:
                logger.warning(f"插件 ID 重复，已忽略 {plugin_path}：{plugin_id}")
                continue

            collected[plugin_id] = {
                "webui_root": webui_root,
                "name": str(manifest.get("name") or plugin_id),
                "description": str(manifest.get("description") or ""),
            }

    return collected


def _resolve_safe_webui_file_path(webui_root: Path, file_path: str) -> Optional[Path]:
    """把请求路径限制在插件 webui 目录内，越界返回 None（与静态页面服务保持一致）。"""
    static_root = webui_root.resolve()

    try:
        candidate_path = (static_root / file_path).resolve()
        candidate_path.relative_to(static_root)
    except (OSError, RuntimeError, ValueError):
        logger.warning(f"插件 WebUI 路径遍历检测: {file_path}")
        return None

    return candidate_path


@router.get("")
async def list_plugin_webui_pages() -> Dict[str, Any]:
    """列出所有提供了 WebUI 页面的插件。"""
    plugins = [
        {
            "plugin_id": plugin_id,
            "name": info["name"],
            "description": info["description"],
            "entry": f"/api/webui/plugins/ui/{plugin_id}/",
        }
        for plugin_id, info in sorted(_collect_webui_plugins().items())
    ]
    return {"success": True, "plugins": plugins}


@router.get("/{plugin_id}/{file_path:path}")
async def serve_plugin_webui_file(plugin_id: str, file_path: str) -> FileResponse:
    """提供插件 webui 目录下的静态文件；file_path 为空时回落到 index.html。"""
    plugin_info = _collect_webui_plugins().get(plugin_id)
    if plugin_info is None:
        raise HTTPException(status_code=404, detail="插件未提供 WebUI 页面")

    resolved_path = _resolve_safe_webui_file_path(plugin_info["webui_root"], file_path or PLUGIN_WEBUI_INDEX)
    if resolved_path is None or not resolved_path.is_file():
        raise HTTPException(status_code=404, detail="插件 WebUI 文件不存在")

    return FileResponse(resolved_path)
