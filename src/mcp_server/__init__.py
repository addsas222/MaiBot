"""进程内 MCP 服务器包。

麦麦作为 MCP 服务器对外提供工具，供 opencode 等外部 MCP 客户端连接调用；
与 ``src.mcp_module`` 的 MCP 客户端能力互为反向。

业务运行时应通过 ``src.mcp_server.service.get_mcp_host_server_service``
复用进程级服务器实例。
"""

from .service import MCPHostServerService, get_mcp_host_server_service

__all__ = ["MCPHostServerService", "get_mcp_host_server_service"]
