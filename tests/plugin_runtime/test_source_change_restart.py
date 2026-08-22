"""插件源码变更触发的运行时重启不应被文件监视器回调超时取消。"""

import asyncio
import contextlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from src.config.file_watcher import FileChange, FileWatcher
from src.plugin_runtime.integration import PluginRuntimeManager


def _build_manager() -> PluginRuntimeManager:
    """构造未启动的运行时管理器实例。"""
    manager = PluginRuntimeManager()
    manager._started = True
    return manager


def _build_file_change(path_str: str) -> FileChange:
    """构造单个源码变更事件。"""
    return FileChange(
        change_type="modified",
        path=Path(path_str),
    )


def test_source_change_restart_is_detached_from_watcher_timeout(tmp_path):
    """重启放入后台任务，回调立即返回，不被 wait_for 超时取消。"""
    manager = _build_manager()
    plugins_root = tmp_path / "plugins"
    plugins_root.mkdir()

    async def fake_restart(*args, **kwargs):
        # 模拟一次超长重启（超过文件监视器默认回调超时 15s）
        await asyncio.sleep(20)
        return True

    async def scenario():
        with (
            patch.object(manager, "_iter_plugin_dirs", return_value=[plugins_root]),
            patch.object(manager, "_sync_plugin_dependencies", return_value=MagicMock()),
            patch.object(manager, "_restart_supervisors", new=AsyncMock(side_effect=fake_restart)),
        ):
            watcher = FileWatcher(
                paths=[plugins_root],
                debounce_ms=600,
                callback_timeout_s=15.0,
                callback_failure_threshold=3,
                callback_cooldown_s=30.0,
            )
            subscription_id = watcher.subscribe(manager._handle_plugin_source_changes, paths=[plugins_root])
            try:
                # 直接走 watcher 的派发路径，验证回调不会被 15s 超时取消
                await watcher._dispatch_changes([_build_file_change(str(plugins_root / "demo" / "plugin.py"))])
                assert manager._plugin_restart_task is not None
                assert not manager._plugin_restart_task.done(), "重启应仍在后台执行而非被取消"

                # 等重启任务完成，确认成功执行且未被取消
                await asyncio.wait_for(manager._plugin_restart_task, timeout=30)
                manager._restart_supervisors.assert_awaited_once()
            finally:
                watcher.unsubscribe(subscription_id)
                if manager._plugin_restart_task is not None and not manager._plugin_restart_task.done():
                    manager._plugin_restart_task.cancel()

    asyncio.run(scenario())


def test_source_change_restart_skips_while_in_progress(tmp_path):
    """重启进行中的再次变更不会重复触发重启。"""
    manager = _build_manager()
    plugins_root = tmp_path / "plugins"
    plugins_root.mkdir()

    async def scenario():
        manager._plugin_restart_task = asyncio.create_task(asyncio.sleep(60))
        try:
            with (
                patch.object(manager, "_iter_plugin_dirs", return_value=[plugins_root]),
                patch.object(manager, "_sync_plugin_dependencies", new=AsyncMock()) as sync_mock,
            ):
                await manager._handle_plugin_source_changes(
                    [_build_file_change(str(plugins_root / "demo" / "plugin.py"))]
                )
                sync_mock.assert_not_awaited()
        finally:
            manager._plugin_restart_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await manager._plugin_restart_task

    asyncio.run(scenario())
