import ipaddress
from dataclasses import dataclass, field
from typing import Any, Mapping
from urllib.parse import urlparse

from src.common.logger import get_logger
from src.config.model_configs import APIProvider, OpenAICompatibleAuthType

logger = get_logger("llm_models.openai_compat")


def _is_local_host(host: str) -> bool:
    """判断主机名是否为本机/内网地址（这类目标通常只有 http 可用）。"""

    if host in {"localhost", "::1"}:
        return True
    try:
        addr = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return addr.is_loopback or addr.is_private

# OpenCode zen 免费网关只放行 opencode CLI 客户端身份的请求，
# 其余 User-Agent 一律按匿名客户端拒绝（429 FreeUsageLimitError）。
# 版本号需跟随官方 CLI 大版本更新。
OPENCODE_ZEN_HOSTNAME = "opencode.ai"
OPENCODE_CLI_IDENTITY_HEADERS = {
    "User-Agent": "opencode/latest/1.18.18/cli",
    "x-opencode-client": "cli",
}


@dataclass(slots=True)
class OpenAICompatibleClientConfig:
    """OpenAI 兼容客户端的基础配置。"""

    api_key: str
    base_url: str
    default_headers: dict[str, str] = field(default_factory=dict)
    default_query: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class OpenAICompatibleRequestOverrides:
    """单次请求级别的附加配置。"""

    extra_headers: dict[str, str] = field(default_factory=dict)
    extra_query: dict[str, object] = field(default_factory=dict)
    extra_body: dict[str, Any] = field(default_factory=dict)

def normalize_openai_base_url(base_url: str) -> str:
    """规范化 OpenAI 兼容接口的基础地址。

    去掉尾部斜杠；缺少协议前缀时，本机/内网地址补 ``http://``，
    其余（公网目标）一律补 ``https://`` 并记录告警——避免 API Key
    因默认明文 http 而跨网泄露。

    Args:
        base_url: 原始基础地址。

    Returns:
        str: 规范化后的地址。
    """
    base_url = base_url.strip()
    if base_url and "://" not in base_url:
        host = urlparse(f"//{base_url.split('/', 1)[0]}", scheme="").hostname or ""
        if _is_local_host(host):
            base_url = "http://" + base_url
        else:
            logger.warning(
                f"base_url 缺少协议前缀，已按 https:// 补全：{base_url}。"
                "如该目标确实仅支持 http，请显式写出 http:// 前缀"
            )
            base_url = "https://" + base_url
    return base_url.rstrip("/")



def _build_auth_header_value(prefix: str, api_key: str) -> str:
    """构造鉴权请求头的值。

    Args:
        prefix: 请求头前缀。
        api_key: 实际密钥。

    Returns:
        str: 拼接完成的请求头值。
    """
    normalized_prefix = prefix.strip()
    if not normalized_prefix:
        return api_key
    return f"{normalized_prefix} {api_key}"


def build_openai_compatible_client_config(api_provider: APIProvider) -> OpenAICompatibleClientConfig:
    """构建 OpenAI 兼容客户端配置。

    Args:
        api_provider: API 提供商配置。

    Returns:
        OpenAICompatibleClientConfig: 可直接用于初始化 SDK 客户端的配置。
    """
    default_headers = dict(api_provider.default_headers)
    default_query: dict[str, object] = dict(api_provider.default_query)
    client_api_key = api_provider.api_key
    normalized_base_url = normalize_openai_base_url(api_provider.base_url)
    hostname = (urlparse(normalized_base_url).hostname or "").lower()
    if hostname == OPENCODE_ZEN_HOSTNAME or hostname.endswith(f".{OPENCODE_ZEN_HOSTNAME}"):
        default_headers.update(OPENCODE_CLI_IDENTITY_HEADERS)

    if api_provider.auth_type == OpenAICompatibleAuthType.BEARER:
        if (
            api_provider.auth_header_name != "Authorization"
            or api_provider.auth_header_prefix.strip() != "Bearer"
        ):
            client_api_key = ""
            default_headers[api_provider.auth_header_name] = _build_auth_header_value(
                prefix=api_provider.auth_header_prefix,
                api_key=api_provider.api_key,
            )
    elif api_provider.auth_type == OpenAICompatibleAuthType.HEADER:
        client_api_key = ""
        default_headers[api_provider.auth_header_name] = _build_auth_header_value(
            prefix=api_provider.auth_header_prefix,
            api_key=api_provider.api_key,
        )
    elif api_provider.auth_type == OpenAICompatibleAuthType.QUERY:
        client_api_key = ""
        default_query[api_provider.auth_query_name] = api_provider.api_key
    elif api_provider.auth_type == OpenAICompatibleAuthType.NONE:
        client_api_key = ""

    return OpenAICompatibleClientConfig(
        api_key=client_api_key,
        base_url=normalize_openai_base_url(api_provider.base_url),
        default_headers=default_headers,
        default_query=default_query,
    )


def _extract_mapping(value: Any) -> dict[str, Any]:
    """将任意映射值规范化为普通字典。

    Args:
        value: 原始输入值。

    Returns:
        dict[str, Any]: 规范化后的字典。非映射值时返回空字典。
    """
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    return {}


def split_openai_request_overrides(
    extra_params: Mapping[str, Any] | None,
    *,
    reserved_body_keys: set[str] | None = None,
) -> OpenAICompatibleRequestOverrides:
    """拆分单次请求中的头、查询参数和请求体扩展字段。

    Args:
        extra_params: 模型级别或请求级别的附加参数。
        reserved_body_keys: 由 SDK 原生参数承载、因此不应再进入 `extra_body` 的字段集合。

    Returns:
        OpenAICompatibleRequestOverrides: 拆分后的请求覆盖配置。
    """
    raw_params = dict(extra_params or {})
    extra_headers = _extract_mapping(raw_params.pop("headers", None))
    extra_query = _extract_mapping(raw_params.pop("query", None))
    extra_body = _extract_mapping(raw_params.pop("body", None))
    blocked_body_keys = reserved_body_keys or set()

    for key, value in raw_params.items():
        if key in blocked_body_keys:
            continue
        extra_body[key] = value

    return OpenAICompatibleRequestOverrides(
        extra_headers={key: str(value) for key, value in extra_headers.items()},
        extra_query=extra_query,
        extra_body=extra_body,
    )
