"""模型故障率统计服务。

消费 logs/maisaka_prompt/llm_error/ 下的请求快照，按模型聚合
成功（重试后）/最终失败次数与失败率，供 WebUI 模型管理页展示。
带 TTL 缓存避免每次请求重复扫描快照目录。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict

from src.common.logger import get_logger

# 与 src.llm_models.request_snapshot.LLM_REQUEST_LOG_DIR 保持一致；
# 此处本地定义以避免 webui → llm_models 的导入环
_SNAPSHOT_LOG_DIR = (
    Path(__file__).resolve().parents[3] / "logs" / "maisaka_prompt" / "llm_error"
)

logger = get_logger("model_failure_stats")

_CACHE_TTL_SECONDS: float = 60.0
_MAX_SCAN_FILES: int = 2000

_cache_lock = None  # 延迟初始化，避免跨事件循环共享锁
_cached_stats: Dict[str, Any] | None = None
_cached_at: float = 0.0


def _build_stats() -> Dict[str, Any]:
    """扫描快照目录并按模型聚合故障数据。"""

    model_stats: Dict[str, Dict[str, Any]] = {}
    scanned = 0
    if not _SNAPSHOT_LOG_DIR.is_dir():
        return {"models": {}, "scanned": 0, "snapshot_dir": str(_SNAPSHOT_LOG_DIR)}

    for file_path in sorted(_SNAPSHOT_LOG_DIR.rglob("*.json")):
        if scanned >= _MAX_SCAN_FILES:
            break
        scanned += 1
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue

        meta = data.get("metadata") or {}
        if not isinstance(meta, dict):
            continue
        status = str(meta.get("status") or "").strip()
        # 只统计终态：succeeded_after_retry 与 final failure；中间态不计入
        if status not in {"succeeded_after_retry", "failed"}:
            continue
        model_name = str(meta.get("model_name") or "").strip()
        if not model_name:
            continue

        entry = model_stats.setdefault(
            model_name,
            {"model": model_name, "total": 0, "retried_ok": 0, "final_failed": 0},
        )
        entry["total"] += 1
        if status == "succeeded_after_retry":
            entry["retried_ok"] += 1
        else:
            entry["final_failed"] += 1
        updated_at = str(meta.get("updated_at") or meta.get("created_at") or "")
        if updated_at > str(entry.get("last_seen", "")):
            entry["last_seen"] = updated_at

    for entry in model_stats.values():
        total = entry["total"]
        entry["fail_rate"] = round(entry["final_failed"] / total, 4) if total else 0.0

    models_sorted = sorted(model_stats.values(), key=lambda e: (-e["total"], e["model"]))
    return {"models": models_sorted, "scanned": scanned, "snapshot_dir": str(_SNAPSHOT_LOG_DIR)}


def get_model_failure_stats(force_refresh: bool = False) -> Dict[str, Any]:
    """获取模型故障率统计（TTL 缓存）。

    Args:
        force_refresh: 跳过缓存强制重扫。
    """

    global _cached_stats, _cached_at, _cache_lock
    import threading

    if _cache_lock is None:
        _cache_lock = threading.Lock()

    now = time.time()
    with _cache_lock:
        if (
            not force_refresh
            and _cached_stats is not None
            and now - _cached_at < _CACHE_TTL_SECONDS
        ):
            return dict(_cached_stats)
        stats = _build_stats()
        _cached_stats = stats
        _cached_at = now
        logger.debug(f"模型故障率统计已刷新: 扫描 {stats['scanned']} 个快照")
        return dict(stats)


