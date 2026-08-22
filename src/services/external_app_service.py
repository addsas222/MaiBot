"""外部应用（SillyTavern / Agnai）进程管理服务。

以受管子进程方式安装、启动、停止第三方 AI 前端应用，并提供健康检查。
每个应用的安装/启动命令与端口可通过 ``data/external-apps/<app_id>.json``
覆盖；配置了 ``external_url`` 的应用进入"外挂模式"——由用户自行运行，
本服务仅负责收纳展示，不再托管进程。
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from src.common.logger import get_logger
from src.common.version import PROJECT_ROOT

logger = get_logger("services.external_apps")

APPS_ROOT = PROJECT_ROOT / "data" / "external-apps"
ENGINE_STATE_PATH = APPS_ROOT / "_engine_state.json"

# 默认注册表：命令均为 argv 列表，顺序执行；可在 <app_id>.json 中按同名键覆盖
DEFAULT_REGISTRY: Dict[str, Dict[str, Any]] = {
    "sillytavern": {
        "name": "SillyTavern",
        "docs_url": "https://docs.sillytavern.app/",
        "default_port": 8000,
        "install_steps": [
            ["git", "clone", "--depth", "1", "https://github.com/SillyTavern/SillyTavern.git", "."],
            ["npm", "install", "--no-audit", "--no-fund"],
        ],
        "start_cmd": ["node", "server.js"],
    },
    "agnai": {
        "name": "Agnai",
        "docs_url": "https://agnai.chat/",
        "default_port": 3001,
        # 官方 selfhost 路径：pnpm 装依赖 -> parcel+tsc 构建前后端；运行需本机 MongoDB/Redis
        "install_steps": [
            ["git", "clone", "--depth", "1", "https://github.com/agnaistic/agnai.git", "."],
            ["npx", "--yes", "pnpm@10", "install", "--lockfile"],
            ["npm", "run", "build:all"],
        ],
        "start_cmd": ["node", "srv/start.js"],
        "env": {"SELF_HOST": "1"},
        # 运行硬依赖 MongoDB；未监听时用 _infra 下捆绑的二进制自动拉起
        "ensure_mongo_port": 27017,
    },
}

_ACTIVE_STATES = {"installing", "starting"}
_HEALTH_TIMEOUT_SECONDS = 3.0


@dataclass
class _ManagedProcess:
    """一个受管子进程的运行时句柄。"""

    proc: asyncio.subprocess.Process
    log_path: Path
    started_at: float = field(default_factory=time.time)


class ExternalAppService:
    """外部应用安装/启停/健康检查管理器，并维护唯一子内核激活状态。"""

    def __init__(self) -> None:
        self._procs: Dict[str, _ManagedProcess] = {}
        self._op_states: Dict[str, str] = {}  # app_id -> installing/starting
        APPS_ROOT.mkdir(parents=True, exist_ok=True)
        self._active_engine: Optional[str] = self._load_engine_state()

    def _load_engine_state(self) -> Optional[str]:
        try:
            payload = json.loads(ENGINE_STATE_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        engine = str(payload.get("active_engine") or "").strip() if isinstance(payload, dict) else ""
        return engine or None

    def _save_engine_state(self) -> None:
        try:
            ENGINE_STATE_PATH.write_text(
                json.dumps({"active_engine": self._active_engine}, ensure_ascii=False), encoding="utf-8"
            )
        except OSError as exc:
            logger.warning(f"子内核状态写入失败: {exc}")

    def get_active_engine(self) -> Optional[str]:
        """当前激活的子内核（sillytavern/agnai），未启用时为 None。"""
        return self._active_engine

    async def set_active_engine(self, app_id: Optional[str]) -> Dict[str, Any]:
        """切换子内核；同一时刻最多激活一个，切换为 None 表示全部停用。"""
        if app_id is not None:
            self._require_spec(app_id)
        previous = self._active_engine
        self._active_engine = app_id
        self._save_engine_state()
        logger.info(f"子内核状态切换: {previous or '无'} -> {app_id or '无'}")
        return {"success": True, "active_engine": app_id, "previous": previous}

    def is_engine_active(self, app_id: str) -> bool:
        return self._active_engine == app_id

    # ---------- 配置与注册表 ----------

    def _override_path(self, app_id: str) -> Path:
        return APPS_ROOT / f"{app_id}.json"

    def _load_override(self, app_id: str) -> Dict[str, Any]:
        path = self._override_path(app_id)
        if not path.is_file():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning(f"外部应用覆盖配置读取失败，已忽略: {path}（{exc}）")
            return {}
        return payload if isinstance(payload, dict) else {}

    def _effective(self, app_id: str, key: str, default: Any) -> Any:
        return self._load_override(app_id).get(key, default)

    async def save_override(self, app_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
        base = self._load_override(app_id)
        allowed = {"external_url", "port", "install_steps", "start_cmd", "env"}
        merged = {**base, **{k: v for k, v in patch.items() if k in allowed}}
        self._override_path(app_id).write_text(
            json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return await self.app_status(app_id)

    def _is_external_mode(self, app_id: str) -> bool:
        return bool(str(self._effective(app_id, "external_url", "") or "").strip())

    # ---------- 状态 ----------

    async def app_status(self, app_id: str) -> Dict[str, Any]:
        spec = DEFAULT_REGISTRY.get(app_id)
        if not spec:
            raise KeyError(app_id)
        managed = self._procs.get(app_id)
        op = self._op_states.get(app_id, "")
        installed_dir = APPS_ROOT / app_id
        installed = installed_dir.is_dir() and any(installed_dir.iterdir())
        external_url = str(self._effective(app_id, "external_url", "") or "").strip()

        status = "stopped"
        pid: Optional[int] = None
        if managed and managed.proc.returncode is None:
            status = "running"
            pid = managed.proc.pid
        elif op in _ACTIVE_STATES:
            status = op
        elif external_url:
            status = "external"
        elif installed:
            healthy = await self._probe_port(self._port_of(app_id))
            status = "running" if healthy else "stopped"
        return {
            "app_id": app_id,
            "name": spec["name"],
            "docs_url": spec["docs_url"],
            "status": status,
            "pid": pid,
            "installed": installed,
            "port": self._port_of(app_id),
            "external_url": external_url,
            "install_steps": self._effective(app_id, "install_steps", spec["install_steps"]),
            "start_cmd": self._effective(app_id, "start_cmd", spec["start_cmd"]),
            "engine_active": self.is_engine_active(app_id),
        }

    async def list_apps(self) -> List[Dict[str, Any]]:
        result = []
        for app_id in DEFAULT_REGISTRY:
            try:
                result.append(await self.app_status(app_id))
            except Exception as exc:
                logger.warning(f"外部应用状态收集失败: {app_id}（{exc}）")
                result.append({"app_id": app_id, "status": "unknown", "error": str(exc)})
        return result

    def _port_of(self, app_id: str) -> int:
        port = self._effective(app_id, "port", DEFAULT_REGISTRY[app_id]["default_port"])
        try:
            return int(port)
        except (TypeError, ValueError):
            return int(DEFAULT_REGISTRY[app_id]["default_port"])

    def embed_url(self, app_id: str) -> str:
        external_url = str(self._effective(app_id, "external_url", "") or "").strip()
        if external_url:
            return external_url
        return f"http://127.0.0.1:{self._port_of(app_id)}/"

    # ---------- 安装 / 启动 / 停止 ----------

    async def install(self, app_id: str) -> Dict[str, Any]:
        self._require_spec(app_id)
        if app_id in self._procs and self._procs[app_id].proc.returncode is None:
            return {"success": False, "error": "应用正在运行，请先停止再安装"}
        if self._op_states.get(app_id) == "installing":
            return {"success": False, "error": "安装已在进行中"}

        steps = self._effective(app_id, "install_steps", DEFAULT_REGISTRY[app_id]["install_steps"])
        target = APPS_ROOT / app_id
        target.mkdir(parents=True, exist_ok=True)
        log_path = self._log_path(app_id, "install")

        async def runner() -> None:
            self._op_states[app_id] = "installing"
            try:
                await self._run_steps(app_id, steps, target, log_path)
                logger.info(f"外部应用安装完成: {app_id}")
            except Exception as exc:
                logger.error(f"外部应用安装失败: {app_id}（{exc}）")
                self._append_log(log_path, f"\n[安装失败] {exc}\n")
            finally:
                self._op_states.pop(app_id, None)

        asyncio.create_task(runner())
        return {"success": True, "message": "安装任务已启动，请稍后刷新查看状态"}

    async def start(self, app_id: str) -> Dict[str, Any]:
        self._require_spec(app_id)
        existing = self._procs.get(app_id)
        if existing and existing.proc.returncode is None:
            return {"success": True, "message": "应用已在运行"}
        if self._is_external_mode(app_id):
            return {"success": False, "error": "该应用为外挂模式（已配置 external_url），请自行启动"}
        target = APPS_ROOT / app_id
        if not target.is_dir() or not any(target.iterdir()):
            return {"success": False, "error": "尚未安装，请先执行安装"}

        await self._ensure_mongo_if_needed(app_id)

        # 子内核互斥：启动一个受管应用前自动停止另一个
        for other_id in list(self._procs):
            other = self._procs.get(other_id)
            if other_id != app_id and other and other.proc.returncode is None:
                logger.info(f"子内核互斥：自动停止 {other_id}")
                await self.stop(other_id)

        start_cmd = self._effective(app_id, "start_cmd", DEFAULT_REGISTRY[app_id]["start_cmd"])
        env_extra = self._effective(app_id, "env", {}) or {}
        log_path = self._log_path(app_id, "run")
        try:
            proc = await asyncio.create_subprocess_exec(
                *[str(a) for a in start_cmd],
                cwd=str(target),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env={**dict(os.environ), **{str(k): str(v) for k, v in dict(env_extra).items()}},
            )
        except FileNotFoundError as exc:
            return {"success": False, "error": f"启动依赖缺失（请确认 {start_cmd[0]} 可用）: {exc}"}
        self._procs[app_id] = _ManagedProcess(proc=proc, log_path=log_path)
        asyncio.create_task(self._pump_output(app_id, proc, log_path))

        async def reaper() -> None:
            code = await proc.wait()
            logger.info(f"外部应用进程退出: {app_id}（exit={code}）")

        asyncio.create_task(reaper())
        return {"success": True, "pid": proc.pid, "url": self.embed_url(app_id)}

    async def stop(self, app_id: str) -> Dict[str, Any]:
        managed = self._procs.get(app_id)
        if not managed or managed.proc.returncode is not None:
            self._procs.pop(app_id, None)
            return {"success": True, "message": "应用未在运行"}
        managed.proc.terminate()
        try:
            await asyncio.wait_for(managed.proc.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            managed.proc.kill()
            await managed.proc.wait()
        self._procs.pop(app_id, None)
        logger.info(f"外部应用已停止: {app_id}")
        return {"success": True, "message": "已停止"}

    async def _ensure_mongo_if_needed(self, app_id: str) -> None:
        """应用声明了 ensure_mongo_port 且该端口无监听时，用捆绑二进制拉起 mongod。"""
        mongo_port = DEFAULT_REGISTRY[app_id].get("ensure_mongo_port")
        if not mongo_port or await self._probe_port(int(mongo_port)):
            return
        infra = APPS_ROOT / "_infra"
        mongod_bin = infra / "mongodb" / "bin" / "mongod"
        if not mongod_bin.is_file():
            logger.warning(f"MongoDB 未监听且捆绑二进制缺失（{mongod_bin}），{app_id} 可能启动失败")
            return
        db_path = infra / "mongo-data"
        db_path.mkdir(parents=True, exist_ok=True)
        log_path = infra / "mongod.log"
        proc = await asyncio.create_subprocess_exec(
            str(mongod_bin),
            "--dbpath", str(db_path),
            "--port", str(mongo_port),
            "--bind_ip", "127.0.0.1",
            "--fork",
            "--logpath", str(log_path),
        )
        await proc.wait()
        logger.info(f"已自动拉起捆绑 MongoDB（端口 {mongo_port}）")

    # ---------- 健康检查 / 日志 ----------

    async def health(self, app_id: str) -> Dict[str, Any]:
        url = self.embed_url(app_id)
        healthy = await self._probe_port(self._port_of(app_id)) if not self._is_external_mode(app_id) else True
        return {"success": True, "healthy": healthy, "url": url}

    async def read_log(self, app_id: str, kind: str, last_lines: int) -> str:
        path = self._log_path(app_id, kind)
        if not path.is_file():
            return ""
        try:
            content = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return ""
        return "\n".join(content[-max(1, int(last_lines)):])

    # ---------- 内部工具 ----------

    def _require_spec(self, app_id: str) -> None:
        if app_id not in DEFAULT_REGISTRY:
            raise KeyError(f"未知外部应用: {app_id}")

    def _log_path(self, app_id: str, kind: str) -> Path:
        logs_dir = APPS_ROOT / "_logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        return logs_dir / f"{app_id}.{kind}.log"

    @staticmethod
    def _append_log(path: Path, text: str) -> None:
        try:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(text)
        except OSError:
            pass

    async def _run_steps(
        self, app_id: str, steps: List[Any], cwd: Path, log_path: Path
    ) -> None:
        for step in steps:
            argv = [str(a) for a in step]
            self._append_log(log_path, f"\n$ {' '.join(argv)}\n")
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(cwd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )

            async def pump(proc: asyncio.subprocess.Process = proc, log_path: Path = log_path) -> None:
                assert proc.stdout is not None
                async for chunk in proc.stdout:
                    self._append_log(log_path, chunk.decode("utf-8", errors="replace"))

            pump_task = asyncio.create_task(pump())
            try:
                code = await proc.wait()
            finally:
                pump_task.cancel()
            if code != 0:
                raise RuntimeError(f"命令退出码 {code}: {' '.join(argv)}")

    @staticmethod
    async def _pump_output(app_id: str, proc: asyncio.subprocess.Process, log_path: Path) -> None:
        assert proc.stdout is not None
        try:
            async for chunk in proc.stdout:
                ExternalAppService._append_log(log_path, chunk.decode("utf-8", errors="replace"))
        except Exception as exc:
            logger.debug(f"外部应用输出泵结束: {app_id}（{exc}）")

    @staticmethod
    async def _probe_port(port: int) -> bool:
        url = f"http://127.0.0.1:{port}/"
        try:
            async with httpx.AsyncClient(timeout=_HEALTH_TIMEOUT_SECONDS) as client:
                resp = await client.get(url)
            return resp.status_code < 500
        except (httpx.HTTPError, OSError):
            return False


_service: Optional[ExternalAppService] = None


def get_external_app_service() -> ExternalAppService:
    global _service
    if _service is None:
        _service = ExternalAppService()
    return _service
