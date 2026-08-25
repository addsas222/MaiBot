"""进程级 MCP 服务器服务。

麦麦在自身进程内启动一个 Streamable HTTP MCP 服务器（默认 127.0.0.1:8765/mcp），
供 opencode 等外部 MCP 客户端连接调用。工具直接复用麦麦进程内各模块，
因此可主动发送消息、读写配置、管理插件与记忆。

该服务支持按 bot 配置热重载（[mcp.server] 段落变化后自动重启监听）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

import asyncio
import json
import socket
import threading

from src.common.logger import get_logger

if TYPE_CHECKING:
    from src.config.official_configs import MCPHostServerConfig

logger = get_logger("mcp_host_server")

MCP_SERVER_NAME: str = "maibot"
MCP_SERVER_PATH: str = "/mcp"
STARTUP_TIMEOUT_SECONDS: float = 60.0


def _log_serve_task_crash(task: "asyncio.Task[None]") -> None:
    """uvicorn serve 任务退出时的观测回调：取消视为正常关停，其余异常完整记录。"""

    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error(f"MCP 宿主服务器运行中异常退出: {exc}", exc_info=exc)


class MCPHostServerService:
    """在麦麦进程内维护 MCP 服务器生命周期并支持配置热切换。"""

    def __init__(self) -> None:
        self._app: Optional[Any] = None
        self._uvicorn_server: Optional[Any] = None
        self._server_task: Optional[asyncio.Task[None]] = None
        self._config_signature: str = ""
        self._reload_callback_registered: bool = False
        self._status_lock: threading.Lock = threading.Lock()
        self._status_snapshot: dict[str, Any] = {
            "running": False,
            "host": "",
            "port": 0,
            "path": MCP_SERVER_PATH,
            "auth": False,
            "error": "",
        }

    @staticmethod
    def _build_config_signature(server_config: "MCPHostServerConfig") -> str:
        """仅按有效运行时配置生成签名，避免无关字段变化触发重启。"""

        return json.dumps(
            {
                "enable": server_config.enable,
                "host": server_config.host,
                "port": server_config.port,
                "auth_token": server_config.auth_token,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def register_config_reload_callback(self) -> None:
        """注册主配置热重载回调；重复调用不会重复注册。"""

        if self._reload_callback_registered:
            return

        from src.config.config import config_manager

        config_manager.register_reload_callback(self.on_config_reload)
        self._reload_callback_registered = True

    async def on_config_reload(self, changed_scopes: Optional[list[str]] = None) -> None:
        """在 bot 配置变化后按最新配置重启 MCP 服务器。

        重启失败（如新端口被占用）只记录完整异常，不向配置监听器传播——
        宿主是可选组件，其故障不应拖垮配置热重载链路。
        """

        normalized_scopes = {str(scope).strip().lower() for scope in changed_scopes or ("bot",)}
        if "bot" not in normalized_scopes:
            return

        from src.config.config import config_manager

        try:
            await self.restart(config_manager.get_global_config().mcp.server)
        except Exception:
            logger.exception("MCP 宿主服务器随配置重载重启失败，保持关闭状态")

    @staticmethod
    def _build_app(server_config: "MCPHostServerConfig") -> Any:
        """构建并注册全部 MCP 工具。"""

        from mcp.server.mcpserver import MCPServer

        app = MCPServer(name=MCP_SERVER_NAME)

        from .tools_chat import register_chat_tools
        from .tools_config import register_config_tools
        from .tools_memory import register_memory_tools
        from .tools_status import register_status_tools

        register_chat_tools(app)
        register_status_tools(app)
        register_config_tools(app)
        register_memory_tools(app)

        # 工具注册完成后再构建 ASGI 应用，确保会话管理器看到全部工具。
        # 监听地址由 uvicorn 层绑定；mcp 2.x 的 streamable_http_app 不接受 host 参数。
        return app.streamable_http_app(
            streamable_http_path=MCP_SERVER_PATH,
        )

    @staticmethod
    def _wrap_auth(app: Any, server_config: "MCPHostServerConfig") -> Any:
        """在配置了访问令牌时包装 ASGI 应用，校验 Authorization 头。"""

        token = (server_config.auth_token or "").strip()
        if not token:
            return app

        async def auth_app(scope: dict[str, Any], receive: Any, send: Any) -> None:
            if scope["type"] != "http" or not str(scope.get("path", "")).startswith(MCP_SERVER_PATH):
                await app(scope, receive, send)
                return

            headers = dict(scope.get("headers") or [])
            auth_header = headers.get(b"authorization", b"").decode(errors="ignore")
            if auth_header != f"Bearer {token}":
                response_body = b'{"error": "unauthorized"}'
                await send(
                    {
                        "type": "http.response.start",
                        "status": 401,
                        "headers": [
                            (b"content-type", b"application/json"),
                            (b"content-length", str(len(response_body)).encode()),
                        ],
                    }
                )
                await send({"type": "http.response.body", "body": response_body})
                return
            await app(scope, receive, send)

        return auth_app

    async def start(self) -> None:
        """确保 MCP 服务器已按当前配置启动（幂等）。"""

        from src.config.config import global_config

        server_config = global_config.mcp.server
        # 安全防呆（第五轮审计 E3）：未配置访问令牌且监听非环回地址时，
        # 任何可达主机均可调用 MCP 工具；启动时强提示，不静默放行
        if (server_config.auth_token or "").strip() == "" and server_config.host not in {"127.0.0.1", "localhost", "::1"}:
            logger.warning(
                f"MCP 服务器监听非环回地址 {server_config.host}:{server_config.port} 但未配置访问令牌，"
                "任何能访问该端口的主机均可调用 MCP 工具；建议在配置中设置 mcp.server.auth_token"
            )
        signature = self._build_config_signature(server_config)
        if (
            self._uvicorn_server is not None
            and self._server_task is not None
            and not self._server_task.done()
            and signature == self._config_signature
        ):
            return

        await self.stop()
        self._config_signature = signature

        if not server_config.enable:
            self._update_status(running=False, host=server_config.host, port=server_config.port)
            return

        # 端口预检：uvicorn 绑定失败会 sys.exit(1)，SystemExit 作为 BaseException
        # 会沿事件循环提升至进程级、绕过一切 try/except 直接带走主程序。
        # 必须在创建 serve 任务前把这类失败转成普通异常。
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind((server_config.host, server_config.port))
        except OSError as exc:
            raise RuntimeError(
                f"MCP 服务器端口不可用 {server_config.host}:{server_config.port}（可能已被占用）: {exc}"
            ) from exc
        finally:
            probe.close()

        import uvicorn

        app = self._build_app(server_config)
        uvicorn_config = uvicorn.Config(
            self._wrap_auth(app, server_config),
            host=server_config.host,
            port=server_config.port,
            log_level="warning",
        )
        server = uvicorn.Server(uvicorn_config)

        self._app = app
        self._uvicorn_server = server

        async def _serve() -> None:
            """serve 任务内部就地转化 SystemExit：uvicorn 绑定失败等路径会 sys.exit(1)，
            该 BaseException 会被事件循环提升至进程级，绕过一切调用方 try/except。"""

            try:
                await server.serve()
            except SystemExit as exc:
                raise RuntimeError(
                    f"uvicorn serve 提前退出（code={exc.code}），端口 {server_config.host}:{server_config.port} 可能被占用"
                ) from None

        self._server_task = asyncio.create_task(_serve(), name="mcp_host_server")
        # serve() 在独立任务中长期运行：崩溃不能静默，挂回调完整记录
        self._server_task.add_done_callback(_log_serve_task_crash)

        loop = asyncio.get_running_loop()
        deadline = loop.time() + STARTUP_TIMEOUT_SECONDS
        while not server.started:
            if self._server_task.done():
                raise RuntimeError(f"MCP 服务器启动失败: 端口 {server_config.port} 可能已被占用")
            if loop.time() >= deadline:
                # 启动高峰期（a_memorix 预热、插件拉起等）主循环可能被同步阶段饿住，
                # 15~60 秒内未就绪不代表失败：serve 任务仍活着就转后台看门狗等待，
                # 就绪后自动修正状态；调用方按"本次窗口未就绪"处理，不阻断启动。
                asyncio.get_running_loop().create_task(
                    self._late_ready_watchdog(), name="mcp_host_late_ready"
                )
                raise TimeoutError(
                    f"MCP 服务器 {STARTUP_TIMEOUT_SECONDS:.0f} 秒内未就绪（启动高峰竞争），已转后台继续等待"
                )
            await asyncio.sleep(0.05)

        self._update_status(
            running=True,
            host=server_config.host,
            port=server_config.port,
            auth=bool((server_config.auth_token or "").strip()),
            error="",
        )
        logger.info(f"MCP 服务器已启动: http://{server_config.host}:{server_config.port}{MCP_SERVER_PATH}")

    async def _late_ready_watchdog(self) -> None:
        """超时后的后台看门狗：等 serve 任务真正就绪或退出，并修正运行状态。"""

        from src.config.config import global_config

        server = self._uvicorn_server
        if server is None:
            return

        server_config = global_config.mcp.server
        while not server.started:
            if self._server_task is None or self._server_task.done():
                self._update_status(running=False, error="启动失败（serve 任务已退出）")
                logger.error("MCP 宿主服务器在启动窗口后就绪前退出，已确认失败")
                return
            await asyncio.sleep(0.25)
        self._update_status(
            running=True,
            host=server_config.host,
            port=server_config.port,
            auth=bool((server_config.auth_token or "").strip()),
            error="",
        )
        logger.info(
            f"MCP 宿主服务器已在启动窗口后就绪: "
            f"http://{server_config.host}:{server_config.port}{MCP_SERVER_PATH}"
        )

    async def restart(self, server_config: Optional["MCPHostServerConfig"] = None) -> None:
        """按最新配置重启 MCP 服务器。"""

        if server_config is None:
            from src.config.config import global_config

            server_config = global_config.mcp.server
        await self.start()

    async def stop(self) -> None:
        """停止 MCP 服务器并回收任务。"""

        server = self._uvicorn_server
        task = self._server_task
        self._uvicorn_server = None
        self._server_task = None
        self._app = None

        if server is not None:
            try:
                await server.shutdown()
            except Exception as exc:
                logger.warning(f"MCP 服务器关闭失败: {exc}")

        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, RuntimeError, Exception):
                pass

        self._update_status(running=False, error="")

    def get_status_snapshot(self) -> dict[str, Any]:
        """返回可跨线程读取的纯数据服务器状态快照。"""

        with self._status_lock:
            return json.loads(json.dumps(self._status_snapshot, ensure_ascii=False))

    def _update_status(
        self,
        *,
        running: bool,
        host: str = "",
        port: int = 0,
        auth: bool = False,
        error: str = "",
    ) -> None:
        """更新线程安全的状态快照。"""

        with self._status_lock:
            self._status_snapshot = {
                "running": running,
                "host": host,
                "port": port,
                "path": MCP_SERVER_PATH,
                "auth": auth,
                "error": error,
            }


_mcp_host_server_service = MCPHostServerService()


def get_mcp_host_server_service() -> MCPHostServerService:
    """获取进程级 MCP 服务器服务单例。"""

    return _mcp_host_server_service
