"""不兼容插件持久化隔离。

插件因 Manifest 校验失败或依赖缺失而无法加载时，记录其目录内容指纹。
后续启动时若指纹未变（插件未被更新），跳过加载并以单行 info 替代完整错误堆栈；
指纹变化（插件被更新）时自动解除隔离并重试加载。
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger("plugin_quarantine")


def _dir_fingerprint(plugin_dir: Path) -> str:
    """计算插件目录内容的轻量指纹（相对路径+大小 的 SHA-256 前 16 位）。"""
    entries = []
    for f in sorted(plugin_dir.rglob("*")):
        if f.is_file() and "__pycache__" not in str(f):
            stat = f.stat()
            entries.append(f"{f.relative_to(plugin_dir)}:{stat.st_size}")
    return hashlib.sha256("|".join(entries).encode()).hexdigest()[:16]


class PluginQuarantine:
    """持久化的不兼容插件隔离注册表。"""

    def __init__(self, storage_path: Path) -> None:
        self._path = storage_path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._data: Dict[str, dict] = {}
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self._data = raw
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    def _save(self) -> None:
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self._path)

    def check(self, plugin_dir: Path) -> Optional[str]:
        """返回隔离原因；插件文件已变更则自动解除隔离并返回 None。"""
        key = str(plugin_dir.resolve())
        entry = self._data.get(key)
        if entry is None:
            return None
        try:
            current_fp = _dir_fingerprint(plugin_dir)
        except OSError:
            return entry.get("reason", "未知原因")
        if current_fp != entry.get("fingerprint"):
            del self._data[key]
            self._save()
            logger.info(f"插件 {plugin_dir.name} 文件已变更，解除隔离并重试加载")
            return None
        return entry.get("reason", "未知原因")

    def quarantine(self, plugin_dir: Path, reason: str) -> None:
        """记录插件隔离状态。"""
        key = str(plugin_dir.resolve())
        fp = _dir_fingerprint(plugin_dir)
        existing = self._data.get(key)
        if existing and existing.get("fingerprint") == fp and existing.get("reason") == reason:
            return
        self._data[key] = {
            "reason": reason,
            "fingerprint": fp,
            "quarantined_at": time.time(),
        }
        self._save()
