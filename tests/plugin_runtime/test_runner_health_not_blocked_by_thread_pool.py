"""健康检查响应不依赖共享线程池。

插件耗时任务（如 image-recompress 的 Pillow 压缩）通过 asyncio.to_thread 占用
默认线程池，占满时 health 若依赖 to_thread 写诊断文件会排队等待，导致被 Host
误判卡死而整进程重启。本测试验证线程池被占满时健康检查仍能及时响应。
"""

import asyncio
import os
import threading
import time

import pytest

from src.plugin_runtime.protocol.envelope import Envelope, MessageType
from src.plugin_runtime.runner.runner_main import PluginRunner

_POOL_SIZE = min(32, os.cpu_count() + 4)


def _build_runner() -> PluginRunner:
    """构造未连接的 Runner 实例（不发起真实 IPC）。"""
    return PluginRunner(host_address="", session_token="", plugin_dirs=[])


def _health_envelope() -> Envelope:
    """构造 plugin.health 请求信封。"""
    return Envelope(
        request_id=1,
        message_type=MessageType.REQUEST,
        method="plugin.health",
    )


async def _occupy_default_thread_pool() -> tuple[threading.Event, list]:
    """用阻塞任务占满 asyncio 默认线程池，返回放行信号与任务列表。"""
    loop = asyncio.get_running_loop()
    gate = threading.Event()
    tasks = [loop.run_in_executor(None, gate.wait) for _ in range(_POOL_SIZE)]
    await asyncio.sleep(0.1)
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(asyncio.to_thread(time.sleep, 0.05), timeout=0.02)
    return gate, tasks


def test_health_responds_when_thread_pool_exhausted(monkeypatch, tmp_path):
    """线程池被占满时，健康检查仍应在 2 秒内返回。"""

    async def scenario():
        runner = _build_runner()
        monkeypatch.setattr(runner, "_debug_file_path", lambda: tmp_path / "runner_debug.jsonl")
        gate, tasks = await _occupy_default_thread_pool()
        try:
            response = await asyncio.wait_for(runner._handle_health(_health_envelope()), timeout=2)
        finally:
            gate.set()
            await asyncio.gather(*tasks, return_exceptions=True)
        assert response.payload["healthy"] is True

    asyncio.run(scenario())