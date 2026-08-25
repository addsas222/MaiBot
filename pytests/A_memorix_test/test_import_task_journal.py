"""导入任务日记（ImportTaskJournal）行为自检。

覆盖 ponytail 要求的最小检查：中断保留、无错完成清除、孤儿归档、手动删除。
纯文件级逻辑，不依赖 A_memorix 宿主；使用临时目录，与生产日记完全隔离。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from src.services.memory_service import ImportTaskJournal  # noqa: E402


def _fake_response(task_id: str, status: str, progress: float = 0.0) -> dict:
    return {
        "success": True,
        "task_id": task_id,
        "status": status,
        "progress": progress,
        "done_chunks": int(progress * 10),
        "total_chunks": 10,
        "error": "",
    }


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="maibot-journal-test-") as td:
        journal = ImportTaskJournal()
        # 测试隔离：替换存储路径，绝不触碰生产日记文件
        journal._path = lambda: Path(td) / "maibot-task-journal.json"
        path = journal._path()

        # 1) 记录 running 任务
        journal.observe("create", {}, _fake_response("t-1", "running", 0.4))
        entries = [t for t in journal._load() if t["task_id"] == "t-1"]
        assert len(entries) == 1 and entries[0]["status"] == "running", entries
        print(f"1. running 已记录: {entries[0]}")

        # 2) 无错完成 → 清除
        journal.observe("create", {}, _fake_response("t-1", "completed", 1.0))
        assert not [t for t in journal._load() if t["task_id"] == "t-1"], "成功后必须清除"
        print("2. completed 已清除 ✓")

        # 3) 失败 → 保留
        bad = _fake_response("t-2", "failed")
        bad["error"] = "boom"
        journal.observe("create", {}, bad)
        kept = [t for t in journal._load() if t["task_id"] == "t-2"]
        assert kept and kept[0]["status"] == "failed" and kept[0]["error"] == "boom"

        # 4) 异 pid 孤儿归档为 interrupted 并出现在快照中
        raw = json.loads(path.read_text(encoding="utf-8"))
        for t in raw["tasks"]:
            if t["task_id"] == "t-2":
                t["pid"] = os.getpid() + 99999  # 模拟其他进程遗留
                t["status"] = "running"
        path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        snapshot_ids = {e["task_id"] for e in journal.mark_orphans_and_list()}
        assert "t-2" in snapshot_ids
        statuses = {t["task_id"]: t["status"] for t in journal._load()}
        assert statuses["t-2"] == "interrupted", statuses
        print(f"4. 孤儿归档 interrupted ✓ snapshot={sorted(snapshot_ids)}")

        # 5) 手动删除
        assert journal.drop("t-2") is True
        assert journal.drop("t-2") is False
        assert not [t for t in journal._load() if t["task_id"] == "t-2"]
        print("5. 手动删除 ✓")

        # 6) cancel 动作直接移除条目
        journal.observe("create", {}, _fake_response("t-3", "running"))
        journal.observe("cancel", {"task_id": "t-3"}, {"success": True})
        assert not [t for t in journal._load() if t["task_id"] == "t-3"]
        print("6. cancel 清除 ✓")

    print("JOURNAL_SELF_CHECK_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
