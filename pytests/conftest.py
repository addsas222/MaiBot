"""pytests 级共享夹具。

导入任务日记（``ImportTaskJournal``）默认落盘在运行时的 ``data/`` 目录下，属于真实
运行状态：只要某个用例触发过导入启动，日记就会留下记录并在下次运行时被标记为
interrupted，进而污染后续用例（例如 ``/api/webui/memory/import/tasks`` 的列表内容）。
这里统一把日记路径指向临时目录，保证用例既不读也不写生产状态。
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_import_task_journal(tmp_path_factory, monkeypatch) -> Path:
    """把导入任务日记重定向到本次测试自己的临时目录。"""

    from src.services.memory_service import ImportTaskJournal

    journal_dir = tmp_path_factory.mktemp("import-task-journal")
    monkeypatch.setattr(ImportTaskJournal, "_path", lambda self: journal_dir / "maibot-task-journal.json")
    return journal_dir
