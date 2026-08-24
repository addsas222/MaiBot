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
import threading

from src.common.logger import get_logger

if TYPE_CHECKING:
    from src.config.official_configs import MCPHostServerConfig

logger = get_logger("mcp_host_server")

MCP_SERVER_NAME: str = "maibot"
MCP_SERVER_PATH: str = "/mcp"
STARTUP_TIMEOUT_SECONDS: float = 15.0


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
        """在 bot 配置变化后按最新配置重启 MCP 服务器。"""

        normalized_scopes = {str(scope).strip().lower() for scope in changed_scopes or ("bot",)}
        if "bot" not in normalized_scopes:
            return

        from src.config.config import config_manager

        await self.restart(config_manager.get_global_config().mcp.server)

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

        # 工具注册完成后再构建 ASGI 应用，确保会话管理器看到全部工具
        return app.streamable_http_app(
            streamable_http_path=MCP_SERVER_PATH,
            host=server_config.host,
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
        self._server_task = asyncio.create_task(server.serve(), name="mcp_host_server")

        loop = asyncio.get_running_loop()
        deadline = loop.time() + STARTUP_TIMEOUT_SECONDS
        while not server.started:
            if self._server_task.done():
                raise RuntimeError(f"MCP 服务器启动失败: 端口 {server_config.port} 可能已被占用")
            if loop.time() >= deadline:
                raise TimeoutError("MCP 服务器启动超时")
            await asyncio.sleep(0.05)

        self._update_status(
            running=True,
            host=server_config.host,
            port=server_config.port,
            auth=bool((server_config.auth_token or "").strip()),
            error="",
        )
        logger.info(f"MCP 服务器已启动: http://{server_config.host}:{server_config.port}{MCP_SERVER_PATH}")

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
