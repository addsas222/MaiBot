"""运行状态类 MCP 工具。

提供麦麦进程运行状态概览与最近日志读取能力。
"""

from __future__ import annotations

import json
from typing import Any

from src.manager.async_task_manager import async_task_manager
from src.mcp_module.service import get_mcp_service
from src.platform_io.manager import get_platform_io_manager


def register_status_tools(mcp: Any) -> None:
    """注册运行状态类工具。"""

    @mcp.tool()
    def get_bot_status() -> dict[str, Any]:
        """获取麦麦运行状态概览：平台连接、异步任务、MCP 客户端连接等。"""
        from src.config.config import global_config

        platform_io_manager = get_platform_io_manager()
        drivers = []
        for driver in platform_io_manager.driver_registry.list():
            descriptor = driver.descriptor
            drivers.append(
                {
                    "driver_id": descriptor.driver_id,
                    "kind": getattr(descriptor.kind, "value", descriptor.kind),
                    "platform": descriptor.platform,
                    "account_id": descriptor.account_id,
                    "scope": descriptor.scope,
                    "plugin_id": descriptor.plugin_id,
                }
            )
        return {
            "bot_nickname": global_config.bot.nickname,
            "platform_io_started": platform_io_manager.is_started,
            "platform_drivers": drivers,
            "async_tasks": async_task_manager.get_tasks_status(),
            "mcp_client": get_mcp_service().get_status_snapshot(),
        }

    @mcp.tool()
    def get_recent_logs(limit: int = 50, level: str = "") -> list[dict[str, Any]]:
        """读取麦麦最近日志（JSON Lines），可按级别过滤。

        Args:
            limit: 返回条数上限（1-500，默认 50）。
            level: 可选级别过滤，如 INFO / WARNING / ERROR，留空返回全部。
        """
        from src.common.logger import get_file_handler

        normalized_limit = max(1, min(int(limit), 500))
        normalized_level = str(level or "").strip().upper()
        log_path = get_file_handler().current_file
        if log_path is None or not log_path.exists():
            return []

        with open(log_path, "r", encoding="utf-8", errors="replace") as file_obj:
            lines = file_obj.readlines()

        entries: list[dict[str, Any]] = []
        for line in lines[-max(normalized_limit * 20, 500):]:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if normalized_level and str(record.get("level", "")).upper() != normalized_level:
                continue
            entries.append(
                {
                    "timestamp": record.get("timestamp", ""),
                    "level": record.get("level", ""),
                    "logger_name": record.get("logger_name", ""),
                    "event": record.get("event", ""),
                    "exception": record.get("exception", ""),
                }
            )
            if len(entries) >= normalized_limit:
                break
        entries.reverse()
        return entries
