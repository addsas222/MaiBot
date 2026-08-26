"""tracemalloc 内存诊断器（env 门控，默认关闭）。

用法：启动前设置 MAIBOT_TRACEMALLOC=1。
每 30 分钟输出一份报告到 /tmp/tracemalloc-report.txt：
  - 当前内存 Top15 分配点
  - 与上一份快照的增长 Top15（定位泄漏核心证据）
  - gc / 线程概况
"""
import gc
import os
import threading
import tracemalloc
from datetime import datetime

REPORT_PATH = "/tmp/tracemalloc-report.txt"
INTERVAL_SECONDS = 30 * 60
NFRAMES = 10
TOP_N = 15


def _fmt_bytes(n: int) -> str:
    return f"{n / 1024 / 1024:.1f}MB" if n >= 1024 * 1024 else f"{n / 1024:.0f}KB"


def _top_lines(snapshot: tracemalloc.Snapshot, title: str) -> list[str]:
    lines = [f"--- {title} Top{TOP_N} ---"]
    for stat in snapshot.statistics("lineno")[:TOP_N]:
        frame = stat.traceback[0]
        lines.append(
            f"{_fmt_bytes(stat.size):>10}  {stat.count:>8}个对象  "
            f"{frame.filename.split('/src/')[-1] if '/src/' in frame.filename else frame.filename}:{frame.lineno}"
        )
    return lines


def _dump_loop():
    previous: tracemalloc.Snapshot | None = None
    while True:
        threading.Event().wait(INTERVAL_SECONDS)
        gc.collect()
        snapshot = tracemalloc.take_snapshot()
        ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        current, peak = tracemalloc.get_traced_memory()
        lines = [
            f"\n===== {ts} =====",
            f"tracemalloc 当前追踪: {_fmt_bytes(current)}  峰值: {_fmt_bytes(peak)}",
            f"gc 对象数: {len(gc.get_objects())}  线程数: {threading.active_count()}",
        ]
        lines += _top_lines(snapshot, "当前总量")
        if previous is not None:
            diff = snapshot.compare_to(previous, "lineno")
            lines.append(f"--- 与上次快照相比增长 Top{TOP_N} ---")
            grew = [d for d in diff if d.size_diff > 0][:TOP_N]
            if not grew:
                lines.append("（无增长点）")
            for d in grew:
                frame = d.traceback[0]
                lines.append(
                    f"{_fmt_bytes(d.size_diff):>10}  {d.count_diff:>+8}个对象  "
                    f"{frame.filename.split('/src/')[-1] if '/src/' in frame.filename else frame.filename}:{frame.lineno}"
                )
        previous = snapshot
        with open(REPORT_PATH, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")


def start_if_enabled() -> bool:
    """env 门控入口。返回是否已启动。"""
    if os.environ.get("MAIBOT_TRACEMALLOC") != "1":
        return False
    tracemalloc.start(NFRAMES)
    threading.Thread(target=_dump_loop, name="mem-tracer", daemon=True).start()
    return True
