"""tracemalloc 内存诊断器（env 门控，默认关闭，惰性有限窗口）。

用法：启动前设置 MAIBOT_TRACEMALLOC=1。
设计约束：追踪有显著 CPU/内存开销，因此：
  - 启动阶段不追踪（保证 WebUI/主服务正常速度启动）
  - T+30min 开启 5 帧追踪，T+30/60/90min 各取一份快照并输出增长 diff
  - T+90min 自动 tracemalloc.stop()，开销归零
报告输出到 /tmp/tracemalloc-report.txt。
"""
import gc
import os
import threading
import tracemalloc
from datetime import datetime

REPORT_PATH = "/tmp/tracemalloc-report.txt"
FIRST_SNAPSHOT_DELAY = 30 * 60   # T+30min 首份快照（此时才开启追踪）
SNAPSHOT_INTERVAL = 30 * 60      # 快照间隔
TOTAL_SNAPSHOTS = 3              # 共 3 份：T+30 / T+60 / T+90，之后自动停止追踪
NFRAMES = 5
TOP_N = 15


def _fmt_bytes(n: int) -> str:
    return f"{n / 1024 / 1024:.1f}MB" if n >= 1024 * 1024 else f"{n / 1024:.0f}KB"


def _short(filename: str) -> str:
    return filename.split("/src/")[-1] if "/src/" in filename else filename


def _top_lines(snapshot: tracemalloc.Snapshot, title: str) -> list[str]:
    lines = [f"--- {title} Top{TOP_N} ---"]
    for stat in snapshot.statistics("lineno")[:TOP_N]:
        frame = stat.traceback[0]
        lines.append(
            f"{_fmt_bytes(stat.size):>10}  {stat.count:>8}个对象  {_short(frame.filename)}:{frame.lineno}"
        )
    return lines


def _write_report(lines: list[str]) -> None:
    with open(REPORT_PATH, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def _dump_loop() -> None:
    previous: tracemalloc.Snapshot | None = None
    for i in range(TOTAL_SNAPSHOTS):
        threading.Event().wait(FIRST_SNAPSHOT_DELAY if i == 0 else SNAPSHOT_INTERVAL)
        if i == 0:
            tracemalloc.start(NFRAMES)
        gc.collect()
        snapshot = tracemalloc.take_snapshot()
        ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        current, peak = tracemalloc.get_traced_memory()
        lines = [
            f"\n===== 快照{i + 1}/{TOTAL_SNAPSHOTS} {ts} =====",
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
                    f"{_fmt_bytes(d.size_diff):>10}  {d.count_diff:>+8}个对象  {_short(frame.filename)}:{frame.lineno}"
                )
        _write_report(lines)
        previous = snapshot
    tracemalloc.stop()
    _write_report([f"===== 追踪已自动停止（共 {TOTAL_SNAPSHOTS} 份快照），开销归零 ====="])


def start_if_enabled() -> bool:
    """env 门控入口。返回是否已启用（仅启动调度线程，不立即追踪）。"""
    if os.environ.get("MAIBOT_TRACEMALLOC") != "1":
        return False
    threading.Thread(target=_dump_loop, name="mem-tracer", daemon=True).start()
    return True
