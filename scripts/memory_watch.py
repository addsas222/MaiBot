"""内存占用观测台：启动 MaiBot 组件栈后周期采样 RSS/smaps/tracemalloc，归因增长来源。

用法:
    uv run python scripts/memory_watch.py [--duration 120] [--interval 30] [--top 10]
                                          [--skip memorix,plugins]

设计要点:
- 每轮只与上一轮快照对比并立即释放旧快照，降低观测者效应；
- smaps 按映射聚合 RSS，定位原生（非 Python 堆）增长的载体；
- --skip memorix/plugins 可做组件二分斜率实验；
- 报告输出后以 SIGKILL 硬退整个进程组，防止 runner/后台任务孤儿悬挂脚本。
"""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import faulthandler
import gc
import os
import signal
import sys
import tracemalloc
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

# 诊断钩子：失控时 `kill -USR1 <pid>` 向 stderr 转储全部线程 Python 栈（免 root）
faulthandler.register(signal.SIGUSR1, file=sys.stderr, all_threads=True, chain=False)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def _rss_mb() -> float:
    for line in open("/proc/self/status", encoding="utf-8"):
        if line.startswith("VmRSS"):
            return int(line.split()[1]) / 1024
    return 0.0


def _mapping_key(parts: list[str]) -> str:
    """从 smaps 头行提取映射名：匿名段归 [anon]，文件段取 basename。"""
    if len(parts) >= 6:
        raw = parts[5]
        if raw.startswith("["):
            return raw
        return Path(raw.replace(" (deleted)", "")).name or "[anon]"
    return "[anon]"


def _smaps_rss_by_mapping(min_kb: int = 2048) -> dict[str, float]:
    """解析 /proc/self/smaps，按映射聚合 RSS(MB)，过滤小于阈值的条目。"""
    totals: dict[str, float] = {}
    key: Optional[str] = None
    with open("/proc/self/smaps", encoding="utf-8") as fh:
        for line in fh:
            if "-" in line and ":" not in line.split(" ", 1)[0] and " r" in line:
                key = _mapping_key(line.split())
            elif key is not None and line.startswith("Rss:"):
                kb = int(line.split()[1])
                if kb >= min_kb:
                    totals[key] = totals.get(key, 0.0) + kb / 1024
                key = None
    return dict(sorted(totals.items(), key=lambda kv: -kv[1]))


def _traced_total_mb(snap: tracemalloc.Snapshot) -> float:
    return sum(stat.size for stat in snap.statistics("filename")) / 1048576


def _aggregate_by_module(snap: tracemalloc.Snapshot, top: int) -> list[tuple[str, float]]:
    totals: dict[str, float] = {}
    for stat in snap.statistics("filename"):
        path = str(stat.traceback[0].filename)
        if "/site-packages/" in path:
            key = "[site-packages] " + path.split("/site-packages/")[-1].split("/")[0]
        elif path.startswith("<") or "/lib/python" in path:
            key = "[python-runtime]"
        elif f"{PROJECT_ROOT}/src/" in path:
            key = "src/" + "/".join(path.split(f"{PROJECT_ROOT}/src/")[-1].split("/")[:2])
        else:
            key = "[other]"
        totals[key] = totals.get(key, 0.0) + stat.size
    ranked = sorted(totals.items(), key=lambda kv: -kv[1])
    return [(k, v / 1048576) for k, v in ranked[:top]]


async def main_async(duration: int, interval: int, top: int, skip: str, delay: int) -> None:
    from src.main import MainSystem

    if "plugins" in skip:
        from src.plugin_runtime.integration import get_plugin_runtime_manager

        async def _noop_start_plugins() -> None:
            return

        get_plugin_runtime_manager().start = _noop_start_plugins  # type: ignore[method-assign]
        print("[watch] 已跳过插件运行时启动", flush=True)

    if "memorix" in skip:
        from src.A_memorix.host_service import a_memorix_host_service

        async def _noop_start_memorix() -> None:
            return

        a_memorix_host_service.start = _noop_start_memorix  # type: ignore[method-assign]
        print("[watch] 已跳过 a_memorix 启动", flush=True)

    system = MainSystem()
    print(f"[watch] 初始化组件栈 (pid={os.getpid()}) ...", flush=True)
    try:
        await system.initialize()
    except Exception:
        import traceback

        traceback.print_exc()
        print("[watch] 初始化异常，继续采样已启动组件的斜率", flush=True)

    gc.collect()
    tracemalloc.start(10)

    rss_series: list[tuple[float, float]] = [(0.0, _rss_mb())]
    maps_series: list[tuple[float, dict[str, float]]] = [(0.0, _smaps_rss_by_mapping())]
    heap_series: list[tuple[float, float]] = []
    py_diffs: list[tuple[float, str]] = []
    final_snap: Optional[tracemalloc.Snapshot] = None
    prev_snap: Optional[tracemalloc.Snapshot] = None

    if delay > 0:
        # 错峰测量：等初始化风暴平息后再取基线，用于区分"一次性启动成本"与"持续泄漏"
        print(f"[watch] 延迟 {delay}s 待启动风暴平息 ...", flush=True)
        await asyncio.sleep(delay)
        gc.collect()
    print(f"[watch] T=0s 基线 RSS={rss_series[-1][1]:.0f}MB", flush=True)

    loop = asyncio.get_running_loop()
    end_at = loop.time() + duration
    while loop.time() < end_at:
        await asyncio.sleep(min(interval, max(end_at - loop.time(), 0.1)))
        elapsed = duration - max(end_at - loop.time(), 0)
        gc.collect()

        snap = tracemalloc.take_snapshot()
        heap_mb = _traced_total_mb(snap)
        if prev_snap is not None:
            for stat in snap.compare_to(prev_snap, "lineno")[:5]:
                frame = stat.traceback[0]
                short = frame.filename.replace(str(PROJECT_ROOT) + "/", "")
                py_diffs.append((elapsed, f"{stat.size_diff / 1024:+8.0f}KB | {short}:{frame.lineno}"))
        prev_snap = snap
        final_snap = snap

        maps = _smaps_rss_by_mapping()
        rss_mb = _rss_mb()
        heap_series.append((elapsed, heap_mb))
        maps_series.append((elapsed, maps))
        rss_series.append((elapsed, rss_mb))
        print(
            f"[watch] T={elapsed:.0f}s RSS={rss_mb:.0f}MB "
            f"python堆={heap_mb:.1f}MB 原生≈{rss_mb - heap_mb:.0f}MB",
            flush=True,
        )

    # ================= 报告 =================
    print("\n===== 内存观测报告 =====")
    print("RSS 序列:", " -> ".join(f"{t:.0f}s:{m:.0f}MB" for t, m in rss_series))
    print(f"RSS 净增: {rss_series[-1][1] - rss_series[0][1]:+.1f}MB")
    print(f"Python 堆末值: {heap_series[-1][1]:.1f}MB（净增 {heap_series[-1][1] - heap_series[0][1]:+.1f}MB）")

    base_maps = maps_series[0][1]
    last_maps = maps_series[-1][1]
    native_deltas: list[tuple[str, float]] = []
    for name in set(base_maps) | set(last_maps):
        delta = last_maps.get(name, 0.0) - base_maps.get(name, 0.0)
        if abs(delta) >= 2.0:
            native_deltas.append((name, delta))
    print("\n-- 原生映射增量 Top（首末采样差，≥2MB）--")
    for name, delta in sorted(native_deltas, key=lambda kv: -abs(kv[1]))[:top]:
        print(f"{delta:+9.1f}MB {name}（现 {last_maps.get(name, 0.0):.1f}MB）")

    print("\n-- Python 堆分配增量样例 --")
    for t, d in py_diffs[:top]:
        print(f"T={t:.0f}s {d}")

    if final_snap is not None:
        print("\n-- 模块聚合（末次快照绝对量 Top）--")
        for module, mb in _aggregate_by_module(final_snap, top):
            print(f"{mb:9.1f}MB {module}")

    traced_last = heap_series[-1][1] if heap_series else 0.0
    print(
        f"\ntracemalloc 追踪总量: {traced_last:.1f}MB | RSS: {rss_series[-1][1]:.0f}MB "
        f"| 原生/未追踪: {rss_series[-1][1] - traced_last:.0f}MB"
    )
    before_trim = _rss_mb()
    ctypes.CDLL("libc.so.6").malloc_trim(0)
    after_trim = _rss_mb()
    verdict = (
        "碎片化为主（trim 大幅回收）"
        if before_trim - after_trim > 50
        else "存在真实持有的原生分配"
    )
    print(
        f"malloc_trim: {before_trim:.0f}MB -> {after_trim:.0f}MB "
        f"（回收 {before_trim - after_trim:.0f}MB）=> {verdict}"
    )

    # ---- 收尾：尽力取消遗留任务，随后硬退 ----
    current = asyncio.current_task()
    leftovers = [t for t in asyncio.all_tasks() if t is not current and not t.done()]
    for leftover in leftovers:
        leftover.cancel()
    if leftovers:
        await asyncio.wait(asyncio.gather(*leftovers, return_exceptions=True), timeout=5)


def main() -> None:
    parser = argparse.ArgumentParser(description="MaiBot 内存观测台")
    parser.add_argument("--duration", type=int, default=120, help="观测时长秒数")
    parser.add_argument("--interval", type=int, default=30, help="采样间隔秒数")
    parser.add_argument("--top", type=int, default=10, help="报告中 Top 条目数")
    parser.add_argument("--skip", type=str, default="", help="逗号分隔: memorix,plugins")
    parser.add_argument("--delay", type=int, default=0, help="初始化后延迟秒数再取基线（错峰测稳态斜率）")
    args = parser.parse_args()
    # 总看门狗：观测窗 + 初始化余量之外仍未退出（如收尾挂死）则强制结束，
    # 防止观测脚本自身失控反噬宿主机。
    overall_timeout = args.duration + 180

    async def _bounded() -> None:
        try:
            await asyncio.wait_for(
                main_async(args.duration, args.interval, args.top, args.skip, args.delay),
                timeout=overall_timeout,
            )
        except asyncio.TimeoutError:
            print(f"[watch] 超过总看门狗 {overall_timeout}s 强制退出", flush=True)

    try:
        asyncio.run(_bounded())
    except KeyboardInterrupt:
        print("[watch] 提前中断")
    finally:
        sys.stdout.flush()
        os.killpg(os.getpgrp(), signal.SIGKILL)


if __name__ == "__main__":
    main()
