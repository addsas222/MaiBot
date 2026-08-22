import httpx

_DEFAULT_TIMEOUT = 30.0

_main_client: httpx.AsyncClient | None = None
_webui_client: httpx.AsyncClient | None = None


def get_main_http_client() -> httpx.AsyncClient:
    """获取主事件循环共享的 httpx 客户端（懒创建）"""
    global _main_client

    if _main_client is None or _main_client.is_closed:
        _main_client = httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT)
    return _main_client


async def aclose_main_http_client() -> None:
    """关闭主循环共享客户端（需在主事件循环退出前调用）"""
    global _main_client

    if _main_client is not None and not _main_client.is_closed:
        await _main_client.aclose()
    _main_client = None


def get_webui_http_client() -> httpx.AsyncClient:
    """获取 WebUI 事件循环共享的 httpx 客户端（与主循环客户端相互独立，禁止跨循环使用）"""
    global _webui_client

    if _webui_client is None or _webui_client.is_closed:
        _webui_client = httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT)
    return _webui_client


async def aclose_webui_http_client() -> None:
    """关闭 WebUI 循环共享客户端（需在 WebUI 事件循环退出前调用）"""
    global _webui_client

    if _webui_client is not None and not _webui_client.is_closed:
        await _webui_client.aclose()
    _webui_client = None
